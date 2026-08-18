"""Immutable, privacy-bounded commerce outcomes with verified attribution."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any

import aiomysql
import structlog

from app.config.settings import get_settings
from app.db.pool import acquire
from app.models.commerce_outcome import CommerceOutcomeEvent, source_event_matches
from app.services.episode_service import current_episode, episode_service

logger = structlog.get_logger()

_ATTRIBUTED_DOMAIN_EVENTS = frozenset(
    {
        "ADD_TO_CART",
        "PAYMENT",
        "CANCEL",
        "REFUND",
        "RETURN",
        "REVIEW",
        "SUPPORT_CONTACT",
        "REPEAT_PURCHASE",
    }
)

_PAYLOAD_ALLOWLIST: dict[str, frozenset[str]] = {
    "IMPRESSION": frozenset(
        {
            "position",
            "recommendationSource",
            "retrievalMode",
            "matchType",
            "subjectLabel",
            "recallSource",
            "modelVersion",
        }
    ),
    "CLICK": frozenset(
        {
            "position",
            "recommendationSource",
            "retrievalMode",
            "matchType",
            "subjectLabel",
            "recallSource",
            "modelVersion",
        }
    ),
    "ADD_TO_CART": frozenset({"quantity", "unitPrice", "currency", "cartItemId"}),
    "PAYMENT": frozenset({"quantity", "paidAmount", "currency", "payStatus"}),
    "CANCEL": frozenset({"reasonCode", "orderStatus"}),
    "REFUND": frozenset(
        {"quantity", "refundAmount", "currency", "reasonCode", "refundStatus"}
    ),
    "RETURN": frozenset({"quantity", "reasonCode", "returnStatus"}),
    "REVIEW": frozenset({"rating", "sentiment", "hasMedia"}),
    "SUPPORT_CONTACT": frozenset({"category", "channel", "resolutionStatus"}),
    "REPEAT_PURCHASE": frozenset({"quantity", "paidAmount", "currency"}),
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _stable_event_id(*parts: object) -> str:
    digest = hashlib.sha256("\0".join(str(part) for part in parts).encode()).hexdigest()
    return f"commerce_{digest[:48]}"


def _safe_scalar(value: Any) -> str | int | float | bool | None:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    text = str(value).strip()
    return text[:128] if text else None


class CommerceOutcomeLedgerService:
    async def record_batch(
        self, events: list[CommerceOutcomeEvent]
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for event in events:
            try:
                results.append(await self.record(event))
            except Exception as exc:
                logger.warning(
                    "commerce_outcome_record_failed",
                    event_id=event.eventId,
                    event_type=event.eventType,
                    error=type(exc).__name__,
                )
                results.append(
                    {
                        "eventId": event.eventId,
                        "accepted": False,
                        "status": "PERSISTENCE_FAILED",
                    }
                )
        return results

    async def record(self, event: CommerceOutcomeEvent) -> dict[str, Any]:
        if not get_settings().outcome_ledger_enabled:
            return {
                "eventId": event.eventId,
                "accepted": False,
                "status": "LEDGER_DISABLED",
            }
        if not source_event_matches(event.source, event.eventType):
            return {
                "eventId": event.eventId,
                "accepted": False,
                "status": "SOURCE_EVENT_MISMATCH",
            }
        now = _utc_now()
        oldest = now - timedelta(days=get_settings().commerce_outcome_retention_days)
        if event.occurredAt > now + timedelta(minutes=5) or event.occurredAt < oldest:
            return {
                "eventId": event.eventId,
                "accepted": False,
                "status": "EVENT_TIME_OUT_OF_RANGE",
            }

        attribution: dict[str, Any] | None = None
        if event.eventType in _ATTRIBUTED_DOMAIN_EVENTS and event.requestId:
            attribution = await self._verified_impression(event)
            if attribution is None:
                return {
                    "eventId": event.eventId,
                    "accepted": False,
                    "status": "INVALID_ATTRIBUTION",
                }

        payload = self._sanitize_payload(event.eventType, event.payload)
        payload["attributionStatus"] = (
            "VERIFIED" if attribution is not None else "UNATTRIBUTED"
        )
        if attribution is not None:
            payload["attribution"] = attribution
        effective_run_id = event.runId
        if attribution is not None and attribution.get("runId"):
            # Domain callers cannot choose the Agent run attached to an attributed
            # outcome. It is inherited from the verified impression.
            effective_run_id = str(attribution["runId"])
        persisted_event = event.model_copy(update={"runId": effective_run_id})
        inserted = await self._insert(persisted_event, payload)
        status = "RECORDED" if inserted else "DUPLICATE"
        if inserted:
            await self._project_profile_signal(persisted_event, payload)
        episode_service.record_step(
            "COMMERCE_OUTCOME_RECORDED",
            node_name="commerce_outcome_ledger",
            status="OK",
            output_data={
                "eventType": event.eventType,
                "source": event.source,
                "attributionStatus": payload["attributionStatus"],
                "status": status,
            },
            run_id=effective_run_id,
        )
        return {
            "eventId": event.eventId,
            "accepted": True,
            "status": status,
            "attributionStatus": payload["attributionStatus"],
        }

    async def record_impressions(
        self,
        *,
        user_id: str,
        request_id: str,
        product_ids: list[str],
        recommendation_source: str,
        retrieval_mode: str,
        match_type: str | None,
        subject_label: str | None,
        recall_source: str | None,
        model_version: str | None,
    ) -> None:
        context = current_episode()
        events = [
            CommerceOutcomeEvent(
                eventId=_stable_event_id("IMPRESSION", request_id, product_id, position),
                source="AGENT",
                idempotencyKey=f"impression:{request_id}:{product_id}:{position}",
                eventType="IMPRESSION",
                userId=user_id,
                requestId=request_id,
                runId=context.run_id if context else None,
                productId=product_id,
                position=position,
                payload={
                    "position": position,
                    "recommendationSource": recommendation_source,
                    "retrievalMode": retrieval_mode,
                    "matchType": match_type,
                    "subjectLabel": subject_label,
                    "recallSource": recall_source,
                    "modelVersion": model_version,
                },
            )
            for position, product_id in enumerate(product_ids, start=1)
        ]
        await self.record_batch(events)

    async def record_impression(
        self,
        attribution: dict[str, Any],
        user_id: str,
    ) -> None:
        request_id = str(attribution.get("requestId") or "")
        product_id = str(attribution.get("productId") or "")
        position = int(attribution.get("position") or 0)
        if not request_id or not product_id or position < 1:
            return
        context = current_episode()
        run_id = str(
            attribution.get("runId") or (context.run_id if context else "")
        ) or None
        event = CommerceOutcomeEvent(
            eventId=_stable_event_id("IMPRESSION", request_id, product_id, position),
            source="AGENT",
            idempotencyKey=f"impression:{request_id}:{product_id}:{position}",
            eventType="IMPRESSION",
            userId=user_id,
            requestId=request_id,
            runId=run_id,
            productId=product_id,
            position=position,
            payload={
                "position": position,
                "recommendationSource": attribution.get("source"),
                "retrievalMode": attribution.get("retrievalMode"),
                "matchType": attribution.get("matchType"),
                "subjectLabel": attribution.get("subjectLabel"),
                "recallSource": attribution.get("recallSource"),
                "modelVersion": attribution.get("modelVersion"),
            },
        )
        await self.record(event)

    async def record_click(self, attribution: dict[str, Any], user_id: str) -> None:
        request_id = str(attribution.get("requestId") or "")
        product_id = str(attribution.get("productId") or "")
        position = int(attribution.get("position") or 0)
        if not request_id or not product_id or position < 1:
            return
        context = current_episode()
        run_id = str(
            attribution.get("runId") or (context.run_id if context else "")
        ) or None
        event = CommerceOutcomeEvent(
            eventId=_stable_event_id("CLICK", request_id, product_id, position),
            source="AGENT",
            idempotencyKey=f"click:{request_id}:{product_id}:{position}",
            eventType="CLICK",
            userId=user_id,
            requestId=request_id,
            runId=run_id,
            productId=product_id,
            position=position,
            payload={
                "position": position,
                "recommendationSource": attribution.get("source"),
                "retrievalMode": attribution.get("retrievalMode"),
                "matchType": attribution.get("matchType"),
                "subjectLabel": attribution.get("subjectLabel"),
                "recallSource": attribution.get("recallSource"),
                "modelVersion": attribution.get("modelVersion"),
            },
        )
        await self.record(event)

    async def record_support_contact(
        self,
        *,
        user_id: str,
        case_id: str,
        category: str,
        order_id: str,
        order_item_id: str,
        product_id: str,
        sku_key: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """Project one Java-verified support case into the immutable ledger."""

        event = CommerceOutcomeEvent(
            eventId=_stable_event_id(
                "SUPPORT_CONTACT", case_id, order_id, order_item_id
            ),
            source="SUPPORT",
            idempotencyKey=(
                f"support-contact:{case_id}:{order_item_id}"
            )[:160],
            eventType="SUPPORT_CONTACT",
            userId=user_id,
            runId=run_id,
            productId=product_id,
            skuKey=sku_key,
            orderId=order_id,
            payload={
                "category": category,
                "channel": "AGENT_SUPPORT_CASE",
                "resolutionStatus": "OPEN",
            },
        )
        return await self.record(event)

    async def _verified_impression(
        self, event: CommerceOutcomeEvent
    ) -> dict[str, Any] | None:
        async with acquire() as cur:
            await cur.execute(
                """
                SELECT position, source, retrieval_mode, match_type,
                       recall_source, model_version, run_id
                FROM agent_recommendation_event
                WHERE user_id=%s AND request_id=%s AND product_id=%s
                  AND position=%s AND event_type='IMPRESSION'
                  AND occurred_at>=DATE_SUB(NOW(3), INTERVAL %s DAY)
                LIMIT 1
                """,
                (
                    event.userId,
                    event.requestId,
                    event.productId,
                    event.position,
                    get_settings().commerce_outcome_attribution_days,
                ),
            )
            row = await cur.fetchone()
        if not row:
            return None
        return {
            "position": int(row.get("position") or 0),
            "source": str(row.get("source") or "")[:40],
            "retrievalMode": str(row.get("retrieval_mode") or "text")[:20],
            "matchType": row.get("match_type"),
            "recallSource": row.get("recall_source"),
            "modelVersion": row.get("model_version"),
            "runId": row.get("run_id"),
        }

    async def _insert(
        self, event: CommerceOutcomeEvent, payload: dict[str, Any]
    ) -> bool:
        occurred_at = event.occurredAt.astimezone(timezone.utc).replace(tzinfo=None)
        try:
            async with acquire() as cur:
                await cur.execute(
                    """
                INSERT INTO commerce_outcome_ledger
                    (event_id,source,idempotency_key,event_type,user_id,
                     request_id,run_id,pilot_batch_id,product_id,sku_key,order_id,payload_json,
                     occurred_at,created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,
                        (SELECT r.pilot_batch_id FROM agent_run r WHERE r.run_id=%s),
                        %s,%s,%s,%s,%s,NOW(3))
                """,
                    (
                        event.eventId,
                        event.source,
                        event.idempotencyKey,
                        event.eventType,
                        event.userId,
                        event.requestId,
                        event.runId,
                        event.runId,
                        event.productId,
                        event.skuKey,
                        event.orderId,
                        json.dumps(
                            payload,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            allow_nan=False,
                        ),
                        occurred_at,
                    ),
                )
                return True
        except aiomysql.IntegrityError as exc:
            if exc.args and int(exc.args[0]) == 1062:
                return False
            raise

    @staticmethod
    def _sanitize_payload(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        allowed = _PAYLOAD_ALLOWLIST.get(event_type, frozenset())
        sanitized: dict[str, Any] = {}
        for key, value in payload.items():
            safe_value = _safe_scalar(value)
            if key in allowed and safe_value is not None:
                sanitized[key] = safe_value
        return sanitized

    @staticmethod
    async def _project_profile_signal(
        event: CommerceOutcomeEvent, payload: dict[str, Any]
    ) -> None:
        if not event.productId:
            return
        strength_by_event = {
            "CLICK": 0.20,
            "ADD_TO_CART": 0.40,
            "PAYMENT": 0.80,
            "REPEAT_PURCHASE": 1.0,
            "REFUND": 0.65,
            "RETURN": 0.80,
            "SUPPORT_CONTACT": 0.35,
        }
        strength = strength_by_event.get(event.eventType)
        kind = "product"
        if event.eventType in {"REFUND", "RETURN", "SUPPORT_CONTACT"}:
            kind = "negativeProduct"
        elif event.eventType == "REVIEW":
            try:
                rating = int(payload.get("rating") or 0)
            except (TypeError, ValueError):
                return
            if rating >= 4:
                strength = 0.55
            elif rating <= 2:
                strength = 0.70
                kind = "negativeProduct"
            else:
                return
        if strength is None:
            return
        try:
            from app.services.shopping_profile_service import shopping_profile_service

            await shopping_profile_service.record_implicit_signal(
                event.userId,
                kind=kind,
                value=event.productId,
                source=event.eventType,
                strength=strength,
            )
        except Exception as exc:
            logger.warning(
                "shopping_profile_outcome_projection_failed",
                event_type=event.eventType,
                error=type(exc).__name__,
            )


commerce_outcome_ledger_service = CommerceOutcomeLedgerService()
