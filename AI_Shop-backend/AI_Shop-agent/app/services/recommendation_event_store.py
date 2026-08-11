from __future__ import annotations

from datetime import datetime

from app.constants import IMPRESSION_ATTRIBUTION_TTL
from app.db.pool import acquire
from app.services.episode_service import current_episode


class RecommendationEventStore:
    IMPRESSION = "IMPRESSION"
    CLICK = "CLICK"

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
        occurred_at: datetime | None = None,
    ) -> None:
        if not product_ids:
            return
        event_time = occurred_at or datetime.now()
        episode = current_episode()
        run_id = episode.run_id if episode else None
        rows = [
            (
                user_id,
                request_id,
                product_id,
                position,
                source,
                retrieval_mode,
                match_type,
                subject_label,
                recall_source,
                model_version,
                run_id,
                self.IMPRESSION,
                event_time,
            )
            for position, product_id in enumerate(product_ids, start=1)
        ]
        async with acquire() as cur:
            await cur.executemany(
                """
                INSERT INTO agent_recommendation_event
                    (user_id, request_id, product_id, position, source,
                     retrieval_mode, match_type, subject_label, recall_source,
                     model_version, run_id, event_type, occurred_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE event_id=event_id
                """,
                rows,
            )

    async def record_click(
        self,
        user_id: str,
        request_id: str,
        product_id: str,
        position: int,
    ) -> dict | None:
        async with acquire() as cur:
            await cur.execute(
                """
                INSERT INTO agent_recommendation_event
                    (user_id, request_id, product_id, position, source,
                     retrieval_mode, match_type, subject_label, recall_source,
                     model_version, run_id, event_type, occurred_at)
                SELECT impression.user_id, impression.request_id,
                       impression.product_id, impression.position,
                       impression.source,
                       impression.retrieval_mode, impression.match_type,
                       impression.subject_label, impression.recall_source,
                       impression.model_version,
                       impression.run_id,
                       %s, NOW(3)
                FROM agent_recommendation_event AS impression
                WHERE impression.user_id=%s AND impression.request_id=%s
                  AND impression.product_id=%s AND impression.position=%s
                  AND impression.event_type=%s
                  AND impression.occurred_at >= DATE_SUB(
                      NOW(3), INTERVAL %s SECOND)
                LIMIT 1
                ON DUPLICATE KEY UPDATE
                    event_id=agent_recommendation_event.event_id
                """,
                (
                    self.CLICK,
                    user_id,
                    request_id,
                    product_id,
                    position,
                    self.IMPRESSION,
                    IMPRESSION_ATTRIBUTION_TTL,
                ),
            )
            await cur.execute(
                """
                SELECT request_id, product_id, position, source,
                       retrieval_mode, match_type, subject_label, recall_source,
                       model_version, occurred_at
                FROM agent_recommendation_event
                WHERE user_id=%s AND request_id=%s AND product_id=%s
                  AND position=%s AND event_type=%s
                  AND occurred_at >= DATE_SUB(NOW(3), INTERVAL %s SECOND)
                LIMIT 1
                """,
                (
                    user_id,
                    request_id,
                    product_id,
                    position,
                    self.CLICK,
                    IMPRESSION_ATTRIBUTION_TTL,
                ),
            )
            row = await cur.fetchone()
        return self._to_public(row) if row else None

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
                SELECT click.request_id, click.product_id, click.position,
                       click.source, click.retrieval_mode, click.match_type,
                       click.subject_label, click.recall_source,
                       click.model_version, click.occurred_at
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
    def _to_public(row: dict) -> dict:
        occurred_at = row.get("occurred_at")
        return {
            "requestId": str(row.get("request_id") or ""),
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
