"""Auditable product features used by the shopping-decision ranker.

Only attributes returned by the Java-owned product snapshot are automatically
marked VERIFIED. Free-text/model enrichment can be written as DRAFT later, but
the serving path never turns a draft into a user-facing guarantee.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import structlog

from app.db.pool import acquire

logger = structlog.get_logger()

_KEY_ALIASES = (
    ("brand", ("品牌", "brand")),
    ("performance", ("cpu", "处理器", "显卡", "gpu", "性能", "芯片")),
    ("memory", ("内存", "存储", "硬盘", "ram", "rom")),
    ("battery", ("电池", "续航", "电量")),
    ("portability", ("重量", "轻薄", "尺寸", "便携")),
    ("noise_cancellation", ("降噪", "anc")),
    ("material", ("材质", "面料", "皮质")),
    ("capacity", ("容量", "容积", "升数")),
    ("size", ("尺码", "大小", "尺寸")),
    ("color", ("颜色", "色")),
)


def _feature_key(name: str) -> str:
    lowered = name.strip().lower()
    for canonical, aliases in _KEY_ALIASES:
        if any(alias in lowered for alias in aliases):
            return canonical
    compact = "".join(char if char.isalnum() else "_" for char in lowered).strip("_")
    return compact[:64] or "attribute"


def _unique_pairs(values: list[tuple[str, str]]) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for key, value in values:
        normalized = (str(key or "").strip(), str(value or "").strip())
        if not all(normalized) or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def structured_feature_pairs(product: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    """Flatten known product properties into evidence-carrying feature pairs."""
    rows: list[tuple[str, str, dict[str, Any]]] = []
    product_id = str(product.get("product_id") or product.get("productId") or "")
    brand = str(product.get("brand") or "").strip()
    if brand:
        rows.append(
            (
                "brand",
                brand,
                {"type": "product_property", "productId": product_id, "propertyName": "品牌", "propertyValue": brand},
            )
        )

    raw_properties = product.get("property_values") or product.get("propertyValues") or []
    for prop in raw_properties:
        if not isinstance(prop, dict):
            continue
        name = str(prop.get("property_name") or prop.get("propertyName") or "").strip()
        value = str(prop.get("property_value") or prop.get("propertyValue") or "").strip()
        if name and value:
            rows.append(
                (
                    _feature_key(name),
                    value,
                    {"type": "product_property", "productId": product_id, "propertyName": name[:80], "propertyValue": value[:255]},
                )
            )

    # Detail snapshots group values by property. Supporting both shapes makes
    # enrichment independent of whether recall used snapshotBatch or getDetail.
    for prop in product.get("properties") or []:
        if not isinstance(prop, dict):
            continue
        name = str(prop.get("propertyName") or prop.get("property_name") or "").strip()
        for raw in prop.get("propertyValues") or prop.get("property_values") or []:
            value = raw.get("propertyValue") if isinstance(raw, dict) else raw
            value = str(value or "").strip()
            if name and value:
                rows.append(
                    (
                        _feature_key(name),
                        value,
                        {"type": "product_property", "productId": product_id, "propertyName": name[:80], "propertyValue": value[:255]},
                    )
                )
    deduped: list[tuple[str, str, dict[str, Any]]] = []
    seen: set[tuple[str, str]] = set()
    for key, value, evidence in rows:
        pair = (key, value)
        if pair not in seen:
            seen.add(pair)
            deduped.append((key, value, evidence))
    return deduped[:30]


class ProductDecisionFeatureService:
    async def ensure_structured_features(self, product: dict[str, Any]) -> list[dict[str, Any]]:
        product_id = str(product.get("product_id") or product.get("productId") or "").strip()
        if not product_id:
            return []
        pairs = structured_feature_pairs(product)
        if not pairs:
            return []
        try:
            async with acquire() as cur:
                for key, value, evidence in pairs:
                    await cur.execute(
                        """
                        INSERT INTO agent_product_decision_feature
                            (product_id, feature_key, feature_value, source_type,
                             evidence_json, confidence, review_status, version,
                             valid_from, created_at, updated_at)
                        VALUES (%s,%s,%s,'STRUCTURED_ATTRIBUTE',%s,1.0000,'VERIFIED','v1',
                                NOW(3),NOW(3),NOW(3)) AS incoming
                        ON DUPLICATE KEY UPDATE
                            evidence_json=incoming.evidence_json, confidence=incoming.confidence,
                            review_status='VERIFIED', valid_until=NULL, updated_at=NOW(3)
                        """,
                        (
                            product_id,
                            key,
                            value[:255],
                            json.dumps(evidence, ensure_ascii=False),
                        ),
                    )
        except Exception as exc:
            logger.warning(
                "product_decision_feature_persist_failed",
                product_id=product_id,
                error=type(exc).__name__,
            )
        return [
            {
                "key": key,
                "value": value,
                "source": "STRUCTURED_ATTRIBUTE",
                "reviewStatus": "VERIFIED",
                "evidence": evidence,
            }
            for key, value, evidence in pairs
        ]

    async def verified_features(self, product_id: str) -> list[dict[str, Any]]:
        if not product_id:
            return []
        try:
            async with acquire() as cur:
                await cur.execute(
                    """
                    SELECT feature_key, feature_value, source_type, evidence_json, confidence
                    FROM agent_product_decision_feature
                    WHERE product_id=%s AND review_status='VERIFIED'
                      AND (valid_until IS NULL OR valid_until>NOW(3))
                    ORDER BY feature_key, feature_id
                    LIMIT 40
                    """,
                    (product_id,),
                )
                rows = await cur.fetchall()
        except Exception as exc:
            logger.warning("product_decision_feature_read_failed", error=type(exc).__name__)
            return []
        output: list[dict[str, Any]] = []
        for row in rows or []:
            evidence = row.get("evidence_json")
            if isinstance(evidence, str):
                try:
                    evidence = json.loads(evidence)
                except json.JSONDecodeError:
                    evidence = None
            output.append(
                {
                    "key": str(row.get("feature_key") or ""),
                    "value": str(row.get("feature_value") or ""),
                    "source": str(row.get("source_type") or ""),
                    "confidence": float(row.get("confidence") or 0),
                    "reviewStatus": "VERIFIED",
                    "evidence": evidence if isinstance(evidence, dict) else None,
                }
            )
        return output

    async def annotate_candidates(self, products: list[dict[str, Any]]) -> list[dict[str, Any]]:
        async def annotate(raw: dict[str, Any]) -> dict[str, Any]:
            product = dict(raw)
            direct = await self.ensure_structured_features(product)
            product_id = str(product.get("product_id") or product.get("productId") or "")
            persisted = await self.verified_features(product_id)
            product["decisionFeatures"] = persisted or direct
            return product

        return list(await asyncio.gather(*(annotate(product) for product in products))) if products else []

    @staticmethod
    def evidence_for(features: list[dict[str, Any]], *, limit: int = 3) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for feature in features:
            if str(feature.get("reviewStatus") or "") != "VERIFIED":
                continue
            evidence = feature.get("evidence")
            if isinstance(evidence, dict):
                result.append(evidence)
            if len(result) >= limit:
                break
        return result


product_decision_feature_service = ProductDecisionFeatureService()
