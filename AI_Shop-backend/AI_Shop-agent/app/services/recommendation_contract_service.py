from __future__ import annotations

import json
from typing import Any

from app.domain.recommendation.contracts import (
    AuthoritativeOffer,
    RecommendationCard,
    RecommendationEvidence,
    RecommendationRequest,
    RecommendationResponse,
)
from app.observability.telemetry import current_traceparent


def _product_id(product: dict[str, Any]) -> str:
    return str(
        product.get("product_id")
        or product.get("productId")
        or product.get("id")
        or ""
    ).strip()


def _model_version(product: dict[str, Any], requested: str | None) -> str:
    ranking = product.get("ranking")
    value = (
        requested
        or product.get("model_version")
        or product.get("modelVersion")
        or (ranking.get("policyVersion") if isinstance(ranking, dict) else None)
        or "unknown"
    )
    return str(value).strip() or "unknown"


def _number(product: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = product.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def authoritative_offer(product: dict[str, Any]) -> AuthoritativeOffer:
    product_id = _product_id(product)
    stock = _number(product, "total_stock", "totalStock", "stock")
    raw_in_stock = product.get("in_stock")
    if raw_in_stock is None:
        raw_in_stock = product.get("inStock")
    in_stock = bool(raw_in_stock) if raw_in_stock is not None else stock is not None and stock > 0
    price = _number(
        product,
        "estimated_payable",
        "estimatedPayable",
        "min_price",
        "minPrice",
        "price",
    )
    source = "JAVA_GATEWAY" if any(
        key in product
        for key in (
            "offer_snapshot_id",
            "offerSnapshotId",
            "total_stock",
            "totalStock",
            "quote_expires_at",
            "quoteExpiresAt",
        )
    ) else "UNKNOWN"
    return AuthoritativeOffer(
        productId=product_id,
        skuKey=(product.get("sku_key") or product.get("skuKey")),
        price=price,
        currency=str(product.get("currency") or "CNY"),
        stock=stock,
        inStock=in_stock,
        purchasable=bool(product.get("purchasable", price is not None and in_stock)),
        source=source,
        snapshotId=(product.get("offer_snapshot_id") or product.get("offerSnapshotId")),
        quoteExpiresAt=(product.get("quote_expires_at") or product.get("quoteExpiresAt")),
    )


def evidence_for_product(product: dict[str, Any]) -> list[RecommendationEvidence]:
    evidence: list[RecommendationEvidence] = []
    retrieval_mode = str(product.get("retrieval_mode") or product.get("retrievalMode") or "text")
    evidence.append(
        RecommendationEvidence(
            kind="VISUAL_RECALL" if retrieval_mode == "visual" else "TEXT_RECALL",
            source=str(product.get("recall_source") or product.get("decision_recall_source") or "retrieval"),
            reference=_product_id(product),
            score=_number(product, "score", "_score"),
            detail=str(product.get("match_type") or product.get("matchType") or "")[:500] or None,
        )
    )
    ranking = product.get("ranking")
    if isinstance(ranking, dict):
        evidence.append(
            RecommendationEvidence(
                kind="RERANK",
                source=str(ranking.get("policyVersion") or "ranking-policy"),
                score=float(ranking.get("utilityScore")) if ranking.get("utilityScore") is not None else None,
                detail="可解释排序策略",
            )
        )
    recommendation = product.get("recommendation")
    if isinstance(recommendation, dict):
        for item in recommendation.get("evidence") or []:
            if isinstance(item, dict):
                evidence.append(
                    RecommendationEvidence(
                        kind="CONSTRAINT",
                        source=str(item.get("source") or "verified-product-fact"),
                        reference=str(item.get("key") or "") or None,
                        detail=str(item.get("value") or item.get("reason") or "")[:500] or None,
                    )
                )
    offer = authoritative_offer(product)
    evidence.append(
        RecommendationEvidence(
            kind="OFFER",
            source=offer.source,
            reference=offer.snapshot_id,
            detail="价格与库存来自 Java 权威 Gateway" if offer.source == "JAVA_GATEWAY" else "未发现权威报价快照",
            supports_claim=offer.source == "JAVA_GATEWAY",
        )
    )
    return evidence[:30]


def build_response(
    request: RecommendationRequest,
    *,
    run_id: str,
    products: list[dict[str, Any]],
    status: str | None = None,
    catalog_version: str | None = None,
    degradation: str | None = None,
    fallback_used: bool = False,
    trace: dict[str, Any] | None = None,
    message: str | None = None,
) -> RecommendationResponse:
    items: list[RecommendationCard] = []
    all_evidence: list[RecommendationEvidence] = []
    for position, product in enumerate(products[:100], start=1):
        product_id = _product_id(product)
        if not product_id:
            continue
        offer = authoritative_offer(product)
        item_evidence = evidence_for_product(product)
        model_version = _model_version(product, request.model_version)
        items.append(
            RecommendationCard(
                productId=product_id,
                productName=str(product.get("product_name") or product.get("productName") or "")[:300],
                position=position,
                offer=offer,
                evidence=item_evidence,
                modelVersion=model_version,
                explanation=(product.get("recommendation") if isinstance(product.get("recommendation"), dict) else {}),
                attribution={
                    "requestId": request.request_id,
                    "idempotencyKey": f"{request.request_id}:{product_id}:IMPRESSION",
                },
            )
        )
        all_evidence.extend(item_evidence)
    unique_evidence: list[RecommendationEvidence] = []
    seen: set[tuple[str, str, str | None]] = set()
    for item in all_evidence:
        key = (item.kind, item.source, item.reference)
        if key not in seen:
            seen.add(key)
            unique_evidence.append(item)
    resolved_status = status
    if resolved_status is None:
        resolved_status = "COMPLETED" if items else "NO_RESULT"
    if resolved_status == "COMPLETED" and not any(item.offer.purchasable for item in items):
        resolved_status = "DEGRADED"
        degradation = degradation or "候选商品缺少可购买的 Java 权威报价/库存快照"
    model_version = request.model_version or (items[0].model_version if items else "unknown")
    return RecommendationResponse(
        requestId=request.request_id,
        runId=run_id,
        episodeId=request.episode_id or run_id,
        mode=request.mode,
        status=resolved_status,
        items=items,
        evidence=unique_evidence[:100],
        modelVersion=model_version,
        catalogVersion=catalog_version or request.catalog_version,
        degradation=degradation,
        fallbackUsed=fallback_used,
        retrievalTrace=trace or {},
        traceparent=current_traceparent(),
        message=message,
    )


def parse_legacy_product_cards(value: str | None) -> list[dict[str, Any]]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    if isinstance(parsed, dict) and parsed.get("type") == "PRODUCT_SEARCH_RESULT":
        parsed = parsed.get("products")
    if not isinstance(parsed, list):
        return []
    return [dict(item) for item in parsed if isinstance(item, dict)]
