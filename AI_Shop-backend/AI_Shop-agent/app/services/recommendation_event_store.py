from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from app.constants import IMPRESSION_ATTRIBUTION_TTL
from app.db.pool import acquire
from app.domain.recommendation.contracts import RecommendationEvent
from app.services.episode_service import current_episode


class RecommendationEventConflict(ValueError):
    """An idempotency or client event key was reused for another touchpoint."""


class RecommendationEventStore:
    """Durable recommendation touchpoints with verified attribution."""

    IMPRESSION = "IMPRESSION"
    CLICK = "CLICK"
    ADD_TO_CART = "ADD_TO_CART"

    async def record_impressions(
        self,
        user_id: str,
        request_id: str,
        product_ids: list[str],
        source: str,
        *,
        retrieval_mode: str = "text",
        match_type: str | None = None,
        subject_label: str | None = None,
        recall_source: str | None = None,
        model_version: str | None = None,
        run_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        if not product_ids:
            return
        event_time = self._db_time(occurred_at or datetime.now(timezone.utc))
        episode = current_episode()
        effective_run_id = run_id or (episode.run_id if episode else None)
        rows = []
        for position, product_id in enumerate(product_ids, start=1):
            normalized_product_id = str(product_id or "").strip()
            if not normalized_product_id:
                continue
            event_key = self._stable_key(
                "impression", request_id, normalized_product_id, position
            )
            rows.append(
                (
                    event_key,
                    event_key,
                    user_id,
                    request_id,
                    normalized_product_id,
                    position,
                    (source or "")[:40],
                    (retrieval_mode or "text")[:20],
                    (match_type or "")[:32] or None,
                    (subject_label or "")[:128] or None,
                    (recall_source or "")[:128] or None,
                    (model_version or "")[:128] or None,
                    effective_run_id,
                    self.IMPRESSION,
                    event_time,
                )
            )
        if not rows:
            return
        async with acquire() as cur:
            await cur.executemany(
                """
                INSERT INTO agent_recommendation_event
                    (client_event_id, idempotency_key, user_id, request_id,
                     product_id, position, source, retrieval_mode, match_type,
                     subject_label, recall_source, model_version, run_id,
                     event_type, occurred_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE event_id=event_id
                """,
                rows,
            )

    async def record_event(
        self,
        user_id: str,
        event: RecommendationEvent,
    ) -> dict | None:
        """Persist one API event and return its canonical durable projection."""

        if event.event_type not in {self.IMPRESSION, self.CLICK, self.ADD_TO_CART}:
            return None
        event_time = self._db_time(event.occurred_at)
        async with acquire() as cur:
            await cur.execute(
                """
                SELECT client_event_id, idempotency_key, request_id, run_id,
                       product_id, position, source, retrieval_mode, match_type,
                       subject_label, recall_source, model_version, event_type,
                       occurred_at
                FROM agent_recommendation_event
                WHERE user_id=%s AND idempotency_key=%s
                LIMIT 1
                """,
                (user_id, event.idempotency_key),
            )
            existing = await cur.fetchone()
            if existing:
                # runId/modelVersion are server-owned projections.  A legacy
                # browser retry may carry a request-derived runId after the
                # first write was canonicalized to the impression's run.
                self._assert_same_identity(
                    existing,
                    event,
                    "idempotencyKey",
                    include_server_fields=False,
                )
                return self._to_public(existing)

            await cur.execute(
                """
                SELECT client_event_id, idempotency_key, request_id, run_id,
                       product_id, position, source, retrieval_mode, match_type,
                       subject_label, recall_source, model_version, event_type,
                       occurred_at
                FROM agent_recommendation_event
                WHERE user_id=%s AND client_event_id=%s
                LIMIT 1
                """,
                (user_id, event.event_id),
            )
            existing = await cur.fetchone()
            if existing:
                raise RecommendationEventConflict(
                    "eventId 已被其他推荐事件使用"
                )

            impression = None
            if event.event_type != self.IMPRESSION:
                await cur.execute(
                    """
                    SELECT request_id, product_id, position, source,
                           retrieval_mode, match_type, subject_label,
                           recall_source, model_version, run_id
                    FROM agent_recommendation_event
                    WHERE user_id=%s AND request_id=%s AND product_id=%s
                      AND position=%s AND event_type=%s
                      AND occurred_at >= DATE_SUB(NOW(3), INTERVAL %s SECOND)
                    ORDER BY occurred_at DESC
                    LIMIT 1
                    """,
                    (
                        user_id,
                        event.request_id,
                        event.product_id,
                        event.position,
                        self.IMPRESSION,
                        IMPRESSION_ATTRIBUTION_TTL,
                    ),
                )
                impression = await cur.fetchone()
                if not impression:
                    return None

            source = str(
                (impression or {}).get("source")
                or event.payload.get("source")
                or "client"
            )[:40]
            retrieval_mode = str(
                (impression or {}).get("retrieval_mode")
                or event.payload.get("retrievalMode")
                or "text"
            )[:20]
            match_type = (impression or {}).get("match_type") or event.payload.get(
                "matchType"
            )
            subject_label = (impression or {}).get("subject_label") or event.payload.get(
                "subjectLabel"
            )
            recall_source = (impression or {}).get("recall_source") or event.payload.get(
                "recallSource"
            )
            model_version = str(
                (impression or {}).get("model_version")
                or event.model_version
                or "unknown"
            )[:128]
            effective_run_id = str(
                (impression or {}).get("run_id") or event.run_id
            )[:64]
            await cur.execute(
                """
                INSERT INTO agent_recommendation_event
                    (client_event_id, idempotency_key, user_id, request_id,
                     product_id, position, source, retrieval_mode, match_type,
                     subject_label, recall_source, model_version, run_id,
                     event_type, occurred_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE event_id=event_id
                """,
                (
                    event.event_id,
                    event.idempotency_key,
                    user_id,
                    event.request_id,
                    event.product_id,
                    event.position,
                    source,
                    retrieval_mode,
                    self._bounded_optional(match_type, 32),
                    self._bounded_optional(subject_label, 128),
                    self._bounded_optional(recall_source, 128),
                    model_version,
                    effective_run_id,
                    event.event_type,
                    event_time,
                ),
            )
            await cur.execute(
                """
                SELECT client_event_id, idempotency_key, request_id, run_id,
                       product_id, position, source, retrieval_mode, match_type,
                       subject_label, recall_source, model_version, event_type,
                       occurred_at
                FROM agent_recommendation_event
                WHERE user_id=%s AND idempotency_key=%s
                LIMIT 1
                """,
                (user_id, event.idempotency_key),
            )
            row = await cur.fetchone()
            if row:
                self._assert_same_identity(
                    row,
                    event,
                    "idempotencyKey",
                    include_server_fields=False,
                )
                return self._to_public(row)

            await cur.execute(
                """
                SELECT client_event_id, idempotency_key, request_id, run_id,
                       product_id, position, source, retrieval_mode, match_type,
                       subject_label, recall_source, model_version, event_type,
                       occurred_at
                FROM agent_recommendation_event
                WHERE user_id=%s AND client_event_id=%s
                LIMIT 1
                """,
                (user_id, event.event_id),
            )
            row = await cur.fetchone()
            if row:
                raise RecommendationEventConflict(
                    "eventId 已被其他推荐事件使用"
                )

            await cur.execute(
                """
                SELECT client_event_id, idempotency_key, request_id, run_id,
                       product_id, position, source, retrieval_mode, match_type,
                       subject_label, recall_source, model_version, event_type,
                       occurred_at
                FROM agent_recommendation_event
                WHERE user_id=%s AND request_id=%s AND product_id=%s
                  AND position=%s AND event_type=%s
                LIMIT 1
                """,
                (
                    user_id,
                    event.request_id,
                    event.product_id,
                    event.position,
                    event.event_type,
                ),
            )
            row = await cur.fetchone()
            if row:
                raise RecommendationEventConflict(
                    "同一推荐触点已使用不同幂等键记录"
                )
        return None

    async def record_click(
        self,
        user_id: str,
        request_id: str,
        product_id: str,
        position: int,
    ) -> dict | None:
        event_key = self._stable_key("click", request_id, product_id, position)
        event = RecommendationEvent(
            eventId=event_key,
            idempotencyKey=event_key,
            eventType=self.CLICK,
            requestId=request_id,
            runId=self._run_id_or_request(request_id),
            productId=product_id,
            position=position,
            modelVersion="legacy-attribution",
        )
        return await self.record_event(user_id, event)

    async def validate_batch(self, user_id: str, items: list[dict]) -> list[dict]:
        keys = {
            (
                str(item.get("requestId") or ""),
                str(item.get("productId") or ""),
                int(item.get("position") or 0),
            )
            for item in items
            if item.get("requestId") and item.get("productId") and item.get("position")
        }
        if not keys:
            return []
        tuple_sql = ",".join(["(%s,%s,%s)"] * len(keys))
        params: list[object] = [
            self.IMPRESSION,
            user_id,
            self.CLICK,
            IMPRESSION_ATTRIBUTION_TTL,
            IMPRESSION_ATTRIBUTION_TTL,
        ]
        for request_id, product_id, position in sorted(keys):
            params.extend((request_id, product_id, position))
        async with acquire() as cur:
            await cur.execute(
                f"""
                SELECT click.client_event_id, click.idempotency_key,
                       click.request_id, click.run_id, click.product_id,
                       click.position, click.source, click.retrieval_mode,
                       click.match_type, click.subject_label, click.recall_source,
                       click.model_version, click.event_type, click.occurred_at
                FROM agent_recommendation_event click
                JOIN agent_recommendation_event impression
                  ON impression.user_id=click.user_id
                 AND impression.request_id=click.request_id
                 AND impression.product_id=click.product_id
                 AND impression.position=click.position
                 AND impression.event_type=%s
                WHERE click.user_id=%s AND click.event_type=%s
                  AND click.occurred_at >= DATE_SUB(NOW(3), INTERVAL %s SECOND)
                  AND impression.occurred_at >= DATE_SUB(NOW(3), INTERVAL %s SECOND)
                  AND (click.request_id, click.product_id, click.position)
                      IN ({tuple_sql})
                ORDER BY click.occurred_at DESC
                """,
                tuple(params),
            )
            rows = await cur.fetchall()
        return [self._to_public(row) for row in rows]

    @staticmethod
    def _stable_key(kind: str, request_id: str, product_id: str, position: int) -> str:
        identity = f"{kind}\0{request_id}\0{product_id}\0{position}"
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return f"{kind}:{digest}"

    @staticmethod
    def _assert_same_identity(
        row: dict,
        event: RecommendationEvent,
        collision_field: str,
        *,
        include_server_fields: bool = True,
    ) -> None:
        expected = (
            event.event_type,
            event.request_id,
            event.product_id,
            event.position,
        )
        actual = (
            str(row.get("event_type") or ""),
            str(row.get("request_id") or ""),
            str(row.get("product_id") or ""),
            int(row.get("position") or 0),
        )
        if include_server_fields:
            expected += (event.run_id, event.model_version)
            actual += (
                str(row.get("run_id") or ""),
                str(row.get("model_version") or ""),
            )
        if actual != expected:
            raise RecommendationEventConflict(
                f"{collision_field} 已绑定到不同推荐事件"
            )

    @staticmethod
    def _run_id_or_request(request_id: str) -> str:
        episode = current_episode()
        return str(episode.run_id if episode else request_id)[:64]

    @staticmethod
    def _db_time(value: datetime) -> datetime:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    @staticmethod
    def _bounded_optional(value: Any, max_length: int) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text[:max_length] or None

    @staticmethod
    def _to_public(row: dict) -> dict:
        occurred_at = row.get("occurred_at")
        return {
            "eventId": str(row.get("client_event_id") or ""),
            "idempotencyKey": str(row.get("idempotency_key") or ""),
            "eventType": str(row.get("event_type") or ""),
            "requestId": str(row.get("request_id") or ""),
            "runId": str(row.get("run_id") or ""),
            "productId": str(row.get("product_id") or ""),
            "position": int(row.get("position") or 0),
            "source": str(row.get("source") or "")[:40],
            "retrievalMode": str(row.get("retrieval_mode") or "text")[:20],
            "matchType": row.get("match_type"),
            "subjectLabel": row.get("subject_label"),
            "recallSource": row.get("recall_source"),
            "modelVersion": row.get("model_version"),
            "occurredAt": (
                occurred_at.isoformat(timespec="milliseconds")
                if isinstance(occurred_at, datetime)
                else str(occurred_at or "")
            ),
        }


recommendation_event_store = RecommendationEventStore()
