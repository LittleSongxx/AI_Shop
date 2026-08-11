from __future__ import annotations

import asyncio
import json
import re
import statistics
import time
from collections import defaultdict
from typing import Any

import structlog

from app.config.settings import get_settings
from app.harness.agents.contracts import VerifiedImageContext, VisualSubject
from app.rag.retriever import rag_retriever
from app.services.episode_service import episode_service
from app.services.java_internal_client import java_internal_client
from app.services.product_service import product_service
from app.services.recommendation_attribution_service import (
    recommendation_attribution_service,
)
from app.services.shopping_decision_service import shopping_decision_service
from app.services.shopping_mission_service import (
    apply_explicit_turn,
    empty_shopping_mission,
    mission_is_active,
    shopping_mission_service,
)
from app.services.shopping_profile_service import (
    extract_profile,
    merge_profiles,
    shopping_profile_service,
)
from app.services.tool_invoke_result import ToolInvokeResult
from app.services.visual_selection_store import visual_selection_store
from app.utils.biz_payload import build_product_payload
from app.visual.contracts import VisualIndexHit, VisualProviderError
from app.visual.image_processing import NormalizedImage, normalize_query_image
from app.visual.index import VisualIndexError, visual_product_index
from app.visual.provider import visual_provider

logger = structlog.get_logger()

_GENERIC_VISUAL_TERMS = (
    "查找图中同款或相似商品",
    "帮我找同款",
    "帮我找类似",
    "找同款",
    "找类似",
    "图片中的",
    "图中的",
    "识图",
)
_MAX_BUDGET_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:元|块)?\s*(?:以内|以下|不超过)")
_MIN_BUDGET_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:元|块)?\s*(?:以上|起)")
_RRF_K = 60


class VisualProductSearchService:
    async def search(
        self,
        *,
        user_id: str,
        image_context: VerifiedImageContext,
        query_text: str,
        source_message_id: int | None,
    ) -> ToolInvokeResult:
        started = time.perf_counter()
        settings = get_settings()
        if not settings.visual_search_enabled:
            return ToolInvokeResult(
                content="识图找商品功能当前未启用，请描述品类、颜色、材质或形状。",
                success=False,
                error_code="VISUAL_SEARCH_DISABLED",
            )

        content, headers = await java_internal_client.fetch_agent_image(
            user_id, image_context.asset_id, timeout=15
        )
        content_type = str(headers.get("content-type") or "").split(";", 1)[0].lower()
        if content_type and not content_type.startswith("image/"):
            return ToolInvokeResult(
                content="图片资产校验失败，请重新上传。",
                success=False,
                error_code="VISUAL_IMAGE_INVALID",
            )
        whole_image = normalize_query_image(content)
        selected_subject = image_context.selected_subject
        grounding_trace: dict[str, Any] = {"mode": "selected" if selected_subject else "auto"}

        if selected_subject is None:
            try:
                grounding = await visual_provider.locate_subjects(whole_image.data_uri)
                grounding_trace.update(
                    {
                        "subjectCount": len(grounding.subjects),
                        "model": grounding.metadata.model,
                        "requestId": grounding.metadata.request_id,
                        "usage": grounding.metadata.usage,
                        "attempts": grounding.metadata.attempts,
                    }
                )
                episode_service.record_step(
                    "VISUAL_SUBJECTS_GROUNDED",
                    node_name="visual_search",
                    output_data={
                        **grounding_trace,
                        "subjects": [
                            {
                                "subjectId": subject.subject_id,
                                "label": subject.label,
                                "bbox": list(subject.bbox),
                            }
                            for subject in grounding.subjects
                        ],
                    },
                )
                if len(grounding.subjects) > 1:
                    if not source_message_id:
                        return ToolInvokeResult(
                            content="检测到多个商品主体，但当前会话无法创建选择卡，请重新发送图片。",
                            success=False,
                            error_code="VISUAL_SELECTION_CONTEXT_MISSING",
                        )
                    card = await visual_selection_store.create(
                        user_id=user_id,
                        source_message_id=source_message_id,
                        image_asset_id=image_context.asset_id,
                        original_text=query_text,
                        subjects=grounding.subjects,
                        constraints=await self._filters(user_id, query_text),
                    )
                    episode_service.record_step(
                        "VISUAL_SUBJECT_SELECTION_REQUIRED",
                        node_name="visual_search",
                        output_data={
                            "selectionId": card["selectionId"],
                            "subjectCount": len(card["subjects"]),
                            "expiresAt": card["expiresAt"],
                        },
                    )
                    return ToolInvokeResult(
                        content="图片中有多个商品，请先点击要查找的主体。",
                        biz_type="visual_subject_selection",
                        assistant_cards=json.dumps(card, ensure_ascii=False),
                        retrieval_trace={
                            "mode": "visual",
                            "outcome": "NEEDS_SUBJECT_SELECTION",
                            "grounding": grounding_trace,
                            "latencyMs": _elapsed_ms(started),
                        },
                    )
                if len(grounding.subjects) == 1:
                    selected_subject = grounding.subjects[0]
            except VisualProviderError as exc:
                grounding_trace.update({"degraded": True, "code": exc.code})
                self._record_degraded("grounding", exc.code)

        query_image = normalize_query_image(content, selected_subject)
        filters = await self._filters(user_id, query_text)
        constraints_text = _constraints_text(query_text)
        trace: dict[str, Any] = {
            "mode": "visual",
            "modelVersion": settings.visual_index_model_version,
            "queryImageSha256": image_context.content_sha256,
            "normalizedQuerySha256": query_image.sha256,
            "subject": (
                {
                    "subjectId": selected_subject.subject_id,
                    "label": selected_subject.label,
                    "bbox": list(selected_subject.bbox),
                }
                if selected_subject
                else None
            ),
            "filters": filters,
            "grounding": grounding_trace,
        }

        exact_hits: list[VisualIndexHit] = []
        try:
            exact_hits = await visual_product_index.exact_hash_hits(
                [image_context.content_sha256, whole_image.sha256], filters=filters
            )
        except VisualIndexError as exc:
            trace["indexError"] = str(exc)

        try:
            embedding_tasks = [visual_provider.embed_image(query_image.data_uri)]
            if constraints_text:
                embedding_tasks.append(
                    visual_provider.embed_image(query_image.data_uri, text=constraints_text)
                )
            embedded = await asyncio.gather(*embedding_tasks)
            image_embedding = embedded[0]
            fused_embedding = embedded[1] if len(embedded) > 1 else None
            trace["embedding"] = {
                "model": image_embedding.metadata.model,
                "requestId": image_embedding.metadata.request_id,
                "usage": image_embedding.metadata.usage,
                "attempts": image_embedding.metadata.attempts,
                "fused": bool(fused_embedding),
            }
            episode_service.record_step(
                "VISUAL_QUERY_EMBEDDED",
                node_name="visual_search",
                output_data=trace["embedding"],
            )
        except VisualProviderError as exc:
            trace["embeddingError"] = exc.code
            self._record_degraded("embedding", exc.code)
            if exact_hits:
                return await self._finalize_hits(
                    user_id=user_id,
                    query_text=query_text,
                    query_image=query_image,
                    hits=_dedupe_hits(exact_hits),
                    exact_product_ids={hit.product_id for hit in exact_hits},
                    trace=trace,
                    selected_subject=selected_subject,
                    started=started,
                )
            return await self._understanding_fallback(
                user_id=user_id,
                query_text=query_text,
                query_image=query_image,
                trace=trace,
                selected_subject=selected_subject,
                started=started,
            )

        try:
            recalls = await asyncio.gather(
                visual_product_index.search_knn(
                    image_embedding.vector,
                    document_type="IMAGE",
                    size=settings.visual_image_recall_size,
                    filters=filters,
                ),
                visual_product_index.search_knn(
                    (fused_embedding or image_embedding).vector,
                    document_type="PRODUCT_FUSED",
                    size=settings.visual_fused_recall_size,
                    filters=filters,
                ),
                visual_product_index.search_text(
                    constraints_text,
                    size=settings.visual_text_recall_size,
                    filters=filters,
                ),
            )
        except VisualIndexError as exc:
            trace["indexError"] = str(exc)
            self._record_degraded("index", str(exc))
            if exact_hits:
                return await self._finalize_hits(
                    user_id=user_id,
                    query_text=query_text,
                    query_image=query_image,
                    hits=_dedupe_hits(exact_hits),
                    exact_product_ids={hit.product_id for hit in exact_hits},
                    trace=trace,
                    selected_subject=selected_subject,
                    started=started,
                )
            return await self._understanding_fallback(
                user_id=user_id,
                query_text=query_text,
                query_image=query_image,
                trace=trace,
                selected_subject=selected_subject,
                started=started,
            )

        image_hits, fused_hits, text_hits = recalls
        trace["recall"] = {
            "exact": len(exact_hits),
            "image": len(image_hits),
            "fused": len(fused_hits),
            "text": len(text_hits),
        }
        episode_service.record_step(
            "VISUAL_CANDIDATES_RECALLED",
            node_name="visual_search",
            output_data=trace["recall"],
        )
        merged, merge_trace = _weighted_rrf(
            exact_hits,
            image_hits,
            fused_hits,
            text_hits,
            min_cosine=settings.visual_embedding_min_cosine,
        )
        trace["fusion"] = merge_trace
        episode_service.record_step(
            "VISUAL_RESULTS_FUSED",
            node_name="visual_search",
            output_data=merge_trace,
        )
        if not merged:
            return self._no_match(trace, started)
        return await self._finalize_hits(
            user_id=user_id,
            query_text=query_text,
            query_image=query_image,
            hits=merged,
            exact_product_ids={hit.product_id for hit in exact_hits},
            trace=trace,
            selected_subject=selected_subject,
            started=started,
        )

    async def _finalize_hits(
        self,
        *,
        user_id: str,
        query_text: str,
        query_image: NormalizedImage,
        hits: list[VisualIndexHit],
        exact_product_ids: set[str],
        trace: dict[str, Any],
        selected_subject: VisualSubject | None,
        started: float,
    ) -> ToolInvokeResult:
        settings = get_settings()
        candidates = hits[: settings.visual_rerank_candidate_size]
        reranked, rerank_trace = await self._rerank(query_image, candidates)
        if exact_product_ids:
            exact_hits = [
                hit for hit in reranked if hit.product_id in exact_product_ids
            ]
            reranked = [
                *exact_hits,
                *(hit for hit in reranked if hit.product_id not in exact_product_ids),
            ]
            rerank_trace["exactMatchesPinned"] = len(exact_hits)
        trace["rerank"] = rerank_trace
        episode_service.record_step(
            "VISUAL_CANDIDATES_RERANKED",
            node_name="visual_search",
            status="OK" if not rerank_trace.get("degraded") else "DEGRADED",
            output_data=rerank_trace,
        )
        ordered_ids = [hit.product_id for hit in reranked]
        verified_products = await product_service.load_verified_products(ordered_ids)
        authority_ids = {
            str(product.get("product_id") or "") for product in verified_products
        }
        verified_by_id = {
            str(product.get("product_id") or ""): product
            for product in verified_products
            if product.get("product_id")
        }
        products = [
            verified_by_id[product_id]
            for product_id in ordered_ids
            if product_id in verified_by_id
        ]
        available_ids = {str(product.get("product_id") or "") for product in products}
        trace["snapshot"] = {
            "requested": len(ordered_ids),
            "available": len(products),
            "filtered": len(set(ordered_ids) - available_ids),
            "filteredByAuthority": len(set(ordered_ids) - authority_ids),
            # Price, coupons, stock, brand and explicit mission constraints
            # are evaluated below against the final Java-owned SKU offer. A
            # stale catalogue price must never pre-filter a valid coupon offer.
            "filteredByPreDecisionConstraints": 0,
        }
        episode_service.record_step(
            "VISUAL_PRODUCT_SNAPSHOTS_VERIFIED",
            node_name="visual_search",
            output_data=trace["snapshot"],
        )
        if not products:
            return self._no_match(trace, started)

        hit_by_id = {hit.product_id: hit for hit in reranked}
        subject_label = selected_subject.label if selected_subject else None
        for product in products:
            product_id = str(product.get("product_id") or "")
            hit = hit_by_id[product_id]
            product["retrieval_mode"] = "visual"
            product["match_type"] = (
                "EXACT_IMAGE" if product_id in exact_product_ids else "VISUALLY_SIMILAR"
            )
            product["subject_label"] = subject_label
            product["recall_source"] = hit.recall_source
            product["model_version"] = settings.visual_index_model_version
            product["_recommend_reason"] = (
                "同图商品" if product_id in exact_product_ids else "视觉特征相似"
            )
        return await self._decide_visual_candidates(
            user_id=user_id,
            query_text=query_text,
            candidates=products,
            source="visual_search",
            retrieval_mode="visual",
            selected_subject=selected_subject,
            trace=trace,
            started=started,
            exact_product_ids=exact_product_ids,
            model_version=settings.visual_index_model_version,
        )

    async def _rerank(
        self, query_image: NormalizedImage, hits: list[VisualIndexHit]
    ) -> tuple[list[VisualIndexHit], dict[str, Any]]:
        if not hits:
            return [], {"candidateCount": 0, "degraded": False}
        images = await asyncio.gather(
            *(
                java_internal_client.fetch_product_image(
                    hit.product_id, hit.cover_index or 0, timeout=15
                )
                for hit in hits
            ),
            return_exceptions=True,
        )
        valid_hits: list[VisualIndexHit] = []
        data_uris: list[str] = []
        for hit, result in zip(hits, images, strict=True):
            if isinstance(result, BaseException):
                continue
            try:
                normalized = normalize_query_image(result[0])
            except VisualProviderError:
                continue
            valid_hits.append(hit)
            data_uris.append(normalized.data_uri)
        if len(valid_hits) < 2:
            return hits, {
                "candidateCount": len(hits),
                "rerankedCount": 0,
                "degraded": True,
                "code": "VISUAL_RERANK_IMAGES_UNAVAILABLE",
            }
        try:
            result = await visual_provider.rerank(query_image.data_uri, data_uris)
            ordered = [valid_hits[item.index] for item in result.items]
            ordered_ids = {hit.product_id for hit in ordered}
            ordered.extend(hit for hit in hits if hit.product_id not in ordered_ids)
            return ordered, {
                "candidateCount": len(hits),
                "rerankedCount": len(result.items),
                "model": result.metadata.model,
                "requestId": result.metadata.request_id,
                "usage": result.metadata.usage,
                "relativeScores": [round(item.relevance_score, 6) for item in result.items],
                "attempts": result.metadata.attempts,
                "degraded": False,
            }
        except VisualProviderError as exc:
            self._record_degraded("rerank", exc.code)
            return hits, {
                "candidateCount": len(hits),
                "rerankedCount": 0,
                "degraded": True,
                "code": exc.code,
            }

    async def _understanding_fallback(
        self,
        *,
        user_id: str,
        query_text: str,
        query_image: NormalizedImage,
        trace: dict[str, Any],
        selected_subject: VisualSubject | None,
        started: float,
    ) -> ToolInvokeResult:
        attributes = ""
        try:
            attributes, raw = await visual_provider.describe_product_attributes(
                query_image.data_uri
            )
            trace["understandingFallback"] = {
                "attributes": raw,
                "status": "SUCCESS",
            }
        except VisualProviderError as exc:
            trace["understandingFallback"] = {"status": "FAILED", "code": exc.code}
        query = " ".join(part for part in (attributes, _constraints_text(query_text)) if part).strip()
        if not query:
            trace["outcome"] = "VISUAL_CAPABILITY_UNAVAILABLE"
            trace["latencyMs"] = _elapsed_ms(started)
            return ToolInvokeResult(
                content=(
                    "识图服务暂时不可用，请补充商品品类、颜色、材质或形状后重试。"
                ),
                retrieval_trace=trace,
            )
        try:
            keyword_ids, vector_ids = await asyncio.gather(
                rag_retriever.search_product_keyword_ids(query, 30),
                rag_retriever.search_product_vector_ids(query, 30),
            )
            product_ids = list(dict.fromkeys([*keyword_ids, *vector_ids]))[:30]
            verified_products = await product_service.load_verified_products(product_ids)
        except Exception as exc:
            trace["understandingFallback"]["searchStatus"] = "FAILED"
            trace["understandingFallback"]["searchError"] = type(exc).__name__
            self._record_degraded("text_search", "VISUAL_TEXT_FALLBACK_UNAVAILABLE")
            trace["outcome"] = "VISUAL_CAPABILITY_UNAVAILABLE"
            trace["latencyMs"] = _elapsed_ms(started)
            return ToolInvokeResult(
                content=(
                    "识图和降级检索暂时不可用，请补充商品品类、颜色、材质或形状后重试。"
                ),
                retrieval_trace=trace,
            )
        products = verified_products[: get_settings().visual_rerank_candidate_size]
        if not products:
            return self._no_match(trace, started)
        subject_label = selected_subject.label if selected_subject else None
        settings = get_settings()
        for product in products:
            product["retrieval_mode"] = "visual_understanding"
            product["match_type"] = "IMAGE_UNDERSTANDING"
            product["subject_label"] = subject_label
            product["recall_source"] = "vlm_text_fallback"
            product["model_version"] = settings.visual_grounding_model
            product["_recommend_reason"] = "按图片内容理解推荐"
        return await self._decide_visual_candidates(
            user_id=user_id,
            query_text=query_text,
            candidates=products,
            source="visual_understanding",
            retrieval_mode="visual_understanding",
            selected_subject=selected_subject,
            trace=trace,
            started=started,
            exact_product_ids=set(),
            model_version=settings.visual_grounding_model,
            fallback_intro=(
                "视觉向量服务暂时不可用，以下是按图片内容理解生成的候选，"
                "并非视觉同款。"
            ),
        )

    async def _mission_for_visual_request(
        self, user_id: str, query_text: str
    ) -> dict[str, Any]:
        """Use the same mission contract as text search for visual recall."""
        mission = await shopping_mission_service.load(user_id)
        if mission_is_active(mission):
            return mission
        profile = await shopping_profile_service.get_effective_profile(user_id)
        derived = apply_explicit_turn(
            None,
            profile=profile,
            user_text=query_text,
            message_id=0,
        )
        return derived if mission_is_active(derived) else empty_shopping_mission(profile)

    async def _decide_visual_candidates(
        self,
        *,
        user_id: str,
        query_text: str,
        candidates: list[dict[str, Any]],
        source: str,
        retrieval_mode: str,
        selected_subject: VisualSubject | None,
        trace: dict[str, Any],
        started: float,
        exact_product_ids: set[str],
        model_version: str,
        fallback_intro: str | None = None,
    ) -> ToolInvokeResult:
        """Apply final offer and utility policy after visual-only recall.

        Similarity determines which candidates are worth considering. It never
        independently determines price, sellability, suitability or what the
        user ultimately sees.
        """
        mission = await self._mission_for_visual_request(user_id, query_text)
        decision = await shopping_decision_service.decide(
            user_id=user_id,
            mission=mission,
            candidates=candidates,
            source=source,
            user_text=query_text,
        )
        if not decision.products:
            trace["decision"] = {
                "status": decision.source,
                "requestId": decision.request_id,
                "decisionId": decision.decision_id,
                "candidateCount": len(candidates),
            }
            trace["outcome"] = "NO_ELIGIBLE_OFFER"
            trace["latencyMs"] = _elapsed_ms(started)
            episode_service.record_step(
                "SHOPPING_DECISION_REJECTED",
                node_name="visual_search",
                status="DEGRADED",
                output_data=trace["decision"],
                agent_id="shopping_advisor",
            )
            message = (
                "当前无法核验实时价格、库存或优惠，暂不展示可能无法购买的识图结果。"
                if decision.source == "offer_unavailable"
                else "找到了视觉候选，但没有同时满足当前用途、预算和实时可购买条件的商品。"
            )
            return ToolInvokeResult(
                content=message,
                biz_type="shopping_decision_v2",
                assistant_cards="[]",
                retrieval_trace=trace,
            )

        products = decision.products
        subject_label = selected_subject.label if selected_subject else None
        match_types = {str(product.get("match_type") or "") for product in products}
        match_type = (
            "EXACT_IMAGE"
            if match_types == {"EXACT_IMAGE"}
            else "IMAGE_UNDERSTANDING"
            if match_types == {"IMAGE_UNDERSTANDING"}
            else "VISUALLY_SIMILAR"
        )
        cards, biz_data = build_product_payload(products, request_id=decision.request_id)
        await recommendation_attribution_service.record_impression(
            user_id,
            [str(product.get("product_id") or "") for product in products],
            query=query_text,
            source=source,
            request_id=decision.request_id,
            retrieval_mode=retrieval_mode,
            match_type=match_type,
            subject_label=subject_label,
            recall_source=(
                "weighted_rrf" if source == "visual_search" else "vlm_text_fallback"
            ),
            model_version=model_version,
        )
        trace["recommendation"] = {
            "requestId": decision.request_id,
            "rankingDecisionId": decision.decision_id,
            "productIds": [str(product.get("product_id") or "") for product in products],
            "matchType": match_type,
            "retrievalMode": retrieval_mode,
            "recallSource": (
                "weighted_rrf" if source == "visual_search" else "vlm_text_fallback"
            ),
        }
        trace["outcome"] = "MATCHED"
        trace["latencyMs"] = _elapsed_ms(started)
        exact_count = sum(
            1
            for product in products
            if str(product.get("product_id") or "") in exact_product_ids
        )
        if fallback_intro:
            intro = fallback_intro
        elif exact_count:
            intro = (
                f"找到 {exact_count} 个同图商品和 "
                f"{len(products) - exact_count} 个视觉相似商品。"
            )
        else:
            intro = f"找到 {len(products)} 个视觉相似商品；相似不代表确定同款。"
        intro += " 已按当前用途和实时可购买 SKU 完成筛选。"
        source_refs = [
            {
                "type": "visual_product",
                "productId": str(product.get("product_id") or ""),
                "matchType": product.get("match_type"),
                "modelVersion": model_version,
            }
            for product in products
        ]
        return ToolInvokeResult(
            content=intro,
            biz_type="shopping_decision_v2",
            biz_data=biz_data,
            assistant_cards=cards,
            product_ids=[str(product.get("product_id") or "") for product in products],
            product_names=[str(product.get("product_name") or "") for product in products],
            source_refs=source_refs,
            retrieval_trace=trace,
        )

    async def _filters(self, user_id: str, query_text: str) -> dict[str, Any]:
        durable_profile = await shopping_profile_service.get_effective_profile(user_id)
        explicit_profile = extract_profile(query_text)
        profile = merge_profiles(durable_profile, explicit_profile)
        filters: dict[str, Any] = {}
        budget_max = _match_number(_MAX_BUDGET_RE, query_text)
        budget_min = _match_number(_MIN_BUDGET_RE, query_text)
        if budget_max is None:
            budget_max = _optional_number(profile.get("budgetMax"))
        if budget_min is None:
            budget_min = _optional_number(profile.get("budgetMin"))
        if budget_max is not None:
            filters["budgetMax"] = budget_max
        if budget_min is not None:
            filters["budgetMin"] = budget_min
        brands = [str(brand).strip() for brand in profile.get("brands") or [] if str(brand).strip()]
        excluded_brands = [
            str(brand).strip()
            for brand in profile.get("excludedBrands") or []
            if str(brand).strip()
        ]
        if brands:
            filters["brands"] = brands
        if excluded_brands:
            filters["excludedBrands"] = excluded_brands
        if profile.get("acceptSubstitute") is not None:
            filters["acceptSubstitute"] = bool(profile["acceptSubstitute"])
        if (
            len(brands) == 1
            and not profile.get("acceptSubstitute")
        ):
            filters["brand"] = str(brands[0]).strip()[:100]
        explicit_category = str(explicit_profile.get("category") or "").strip()
        if explicit_category:
            filters["category"] = explicit_category[:80]
        return filters

    def _no_match(self, trace: dict[str, Any], started: float) -> ToolInvokeResult:
        trace["outcome"] = "NO_CONFIDENT_MATCH"
        trace["latencyMs"] = _elapsed_ms(started)
        episode_service.record_step(
            "VISUAL_NO_CONFIDENT_MATCH",
            node_name="visual_search",
            status="DEGRADED",
            output_data={"trace": trace},
        )
        return ToolInvokeResult(
            content="暂未发现可靠的同图或视觉相似商品，可以换一张主体更清晰的图片重试。",
            biz_type="visual_product_search",
            assistant_cards="[]",
            retrieval_trace=trace,
        )

    @staticmethod
    def _record_degraded(capability: str, code: str) -> None:
        episode_service.record_step(
            "VISUAL_CAPABILITY_DEGRADED",
            node_name="visual_search",
            status="DEGRADED",
            output_data={"capability": capability, "code": code},
        )


def _weighted_rrf(
    exact_hits: list[VisualIndexHit],
    image_hits: list[VisualIndexHit],
    fused_hits: list[VisualIndexHit],
    text_hits: list[VisualIndexHit],
    *,
    min_cosine: float,
) -> tuple[list[VisualIndexHit], dict[str, Any]]:
    scores: defaultdict[str, float] = defaultdict(float)
    best: dict[str, VisualIndexHit] = {}
    max_cosine: dict[str, float] = {}
    sources: defaultdict[str, set[str]] = defaultdict(set)
    exact_ids = {hit.product_id for hit in exact_hits if hit.product_id}
    ranked_sources = (
        (exact_hits, 10.0, "exact_hash"),
        (image_hits, 1.4, "image_knn"),
        (fused_hits, 1.2, "product_fused_knn"),
        (text_hits, 0.7, "text"),
    )
    for hits, weight, source in ranked_sources:
        for rank, hit in enumerate(hits, start=1):
            if not hit.product_id:
                continue
            scores[hit.product_id] += weight / (_RRF_K + rank)
            sources[hit.product_id].add(source)
            if hit.cosine is not None:
                max_cosine[hit.product_id] = max(
                    hit.cosine, max_cosine.get(hit.product_id, -1.0)
                )
            current = best.get(hit.product_id)
            if current is None or (
                current.cover_index is None and hit.cover_index is not None
            ):
                best[hit.product_id] = hit
    accepted = [
        product_id
        for product_id in scores
        if product_id in exact_ids or max_cosine.get(product_id, -1.0) >= min_cosine
    ]
    accepted.sort(key=lambda product_id: (-scores[product_id], product_id))
    merged = [
        VisualIndexHit(
            **{
                **best[product_id].__dict__,
                "score": scores[product_id],
                "cosine": max_cosine.get(product_id),
                "recall_source": "+".join(sorted(sources[product_id])),
            }
        )
        for product_id in accepted
    ]
    cosines = list(max_cosine.values())
    trace = {
        "mergedProducts": len(scores),
        "acceptedProducts": len(merged),
        "rejectedByCosine": len(scores) - len(merged),
        "minCosineThreshold": min_cosine,
        "cosine": (
            {
                "min": round(min(cosines), 6),
                "max": round(max(cosines), 6),
                "mean": round(statistics.fmean(cosines), 6),
            }
            if cosines
            else None
        ),
    }
    return merged, trace


def _dedupe_hits(hits: list[VisualIndexHit]) -> list[VisualIndexHit]:
    return list({hit.product_id: hit for hit in reversed(hits) if hit.product_id}.values())[::-1]


def _apply_verified_constraints(
    products: list[dict[str, Any]], filters: dict[str, Any]
) -> list[dict[str, Any]]:
    profile = {
        "budgetMin": filters.get("budgetMin"),
        "budgetMax": filters.get("budgetMax"),
        "brands": list(filters.get("brands") or []),
        "excludedBrands": list(filters.get("excludedBrands") or []),
        "acceptSubstitute": filters.get("acceptSubstitute"),
    }
    filtered = [
        product
        for product in products
        if shopping_profile_service.matches_product(product, profile)
    ]
    category = str(filters.get("category") or "").strip()
    if not category:
        return filtered
    return [
        product
        for product in filtered
        if extract_profile(shopping_profile_service._product_text(product)).get("category")
        == category
    ]


def _constraints_text(query_text: str) -> str:
    text = str(query_text or "")
    for term in _GENERIC_VISUAL_TERMS:
        text = text.replace(term, " ")
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"^[\s,，。；;：:的]+|[\s,，。；;：:]+$", "", text)[:500]


def _match_number(pattern: re.Pattern[str], text: str) -> float | None:
    match = pattern.search(str(text or ""))
    return float(match.group(1)) if match else None


def _optional_number(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


visual_product_search_service = VisualProductSearchService()
