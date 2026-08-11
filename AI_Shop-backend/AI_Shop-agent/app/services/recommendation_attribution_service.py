from __future__ import annotations

import asyncio

import structlog

from app.constants import IMPRESSION_LOG_MAX_PRODUCTS
from app.services.commerce_outcome_ledger_service import (
    commerce_outcome_ledger_service,
)
from app.services.recommendation_event_store import recommendation_event_store
from app.services.redis_service import redis_service

logger = structlog.get_logger()


class RecommendationAttributionService:
    async def record_impression(
        self,
        user_id: str,
        product_ids: list[str],
        *,
        query: str = "",
        source: str = "",
        request_id: str = "",
        retrieval_mode: str = "text",
        match_type: str | None = None,
        subject_label: str | None = None,
        recall_source: str | None = None,
        model_version: str | None = None,
    ) -> None:
        shown = [str(pid) for pid in product_ids if pid][
            :IMPRESSION_LOG_MAX_PRODUCTS
        ]
        if not user_id or not request_id or not shown:
            return
        canonical_source = (source or "")[:40]
        try:
            await recommendation_event_store.record_impressions(
                user_id,
                request_id,
                shown,
                canonical_source,
                retrieval_mode=(retrieval_mode or "text")[:20],
                match_type=(match_type or "")[:32] or None,
                subject_label=(subject_label or "")[:128] or None,
                recall_source=(recall_source or "")[:64] or None,
                model_version=(model_version or "")[:64] or None,
            )
        except Exception as exc:
            # Recommendations remain available, but a missing durable impression
            # can never be promoted to a click or transaction attribution.
            logger.warning(
                "recommendation_impression_persist_failed",
                request_id=request_id,
                product_count=len(shown),
                error=type(exc).__name__,
            )
        else:
            try:
                await commerce_outcome_ledger_service.record_impressions(
                    user_id=user_id,
                    request_id=request_id,
                    product_ids=shown,
                    recommendation_source=canonical_source,
                    retrieval_mode=(retrieval_mode or "text")[:20],
                    match_type=(match_type or "")[:32] or None,
                    subject_label=(subject_label or "")[:128] or None,
                    recall_source=(recall_source or "")[:64] or None,
                    model_version=(model_version or "")[:64] or None,
                )
            except Exception as exc:
                logger.warning(
                    "commerce_impression_ledger_failed",
                    request_id=request_id,
                    error=type(exc).__name__,
                )
        await redis_service.log_impression(
            user_id,
            shown,
            query=query,
            source=canonical_source,
            request_id=request_id,
        )

    async def record_click(
        self,
        user_id: str,
        request_id: str,
        product_id: str,
        position: int,
    ) -> dict | None:
        try:
            attribution = await recommendation_event_store.record_click(
                user_id, request_id, product_id, position
            )
        except Exception as exc:
            logger.warning(
                "recommendation_click_persist_failed",
                request_id=request_id,
                error=type(exc).__name__,
            )
            return None
        if attribution is None:
            return None
        try:
            await commerce_outcome_ledger_service.record_click(attribution, user_id)
        except Exception as exc:
            logger.warning(
                "commerce_click_ledger_failed",
                request_id=request_id,
                error=type(exc).__name__,
            )
        # Redis is a short-lived analytics/cache copy only. Database validation
        # already succeeded, so cache failure must not rewrite the durable truth.
        try:
            await asyncio.wait_for(
                redis_service.log_attributed_click(
                    user_id, request_id, product_id, position
                ),
                timeout=0.1,
            )
        except TimeoutError:
            logger.warning("recommendation_click_cache_timeout")
        return attribution

    async def validate_batch(self, user_id: str, items: list[dict]) -> list[dict]:
        return await recommendation_event_store.validate_batch(user_id, items)


recommendation_attribution_service = RecommendationAttributionService()
