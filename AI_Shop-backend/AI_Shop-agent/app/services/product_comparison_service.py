from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.constants import PRODUCT_STATUS_ON_SALE
from app.services.final_offer_snapshot_service import (
    OfferSnapshotUnavailable,
    final_offer_snapshot_service,
)
from app.services.product_decision_feature_service import product_decision_feature_service
from app.services.product_service import product_service
from app.services.shopping_mission_service import shopping_mission_service
from app.services.tool_invoke_result import ToolInvokeResult

PRODUCT_COMPARISON_TYPE = "PRODUCT_COMPARISON"


class ProductComparisonError(ValueError):
    code = "COMPARISON_INVALID"


class ComparisonCandidateDenied(ProductComparisonError):
    code = "COMPARISON_CANDIDATE_DENIED"


class ComparisonSnapshotMissing(ProductComparisonError):
    code = "COMPARISON_SNAPSHOT_MISSING"


def normalize_comparison_ids(product_ids: list[Any] | None) -> list[str]:
    result: list[str] = []
    for raw in product_ids or []:
        product_id = str(raw or "").strip()
        if product_id and product_id not in result:
            result.append(product_id)
    if not 2 <= len(result) <= 4:
        raise ProductComparisonError("请选择 2 到 4 个不同商品进行比较")
    if any(len(product_id) > 64 for product_id in result):
        raise ProductComparisonError("商品 ID 无效")
    return result


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _availability(snapshot: dict[str, Any]) -> str:
    if str(snapshot.get("status")) != str(PRODUCT_STATUS_ON_SALE):
        return "UNAVAILABLE"
    if snapshot.get("inStock") is False or snapshot.get("in_stock") is False:
        return "OUT_OF_STOCK"
    return "ON_SALE"


def _flatten_properties(snapshot: dict[str, Any]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for prop in snapshot.get("properties") or []:
        if not isinstance(prop, dict):
            continue
        name = str(prop.get("propertyName") or prop.get("property_name") or "").strip()
        values: list[str] = []
        for raw in prop.get("propertyValues") or prop.get("property_values") or []:
            if isinstance(raw, dict):
                value = raw.get("propertyValue") or raw.get("property_value")
            else:
                value = raw
            normalized = str(value or "").strip()
            if normalized and normalized not in values:
                values.append(normalized)
        if name and values:
            result.append({"name": name[:40], "value": " / ".join(values)[:160]})
        if len(result) >= 10:
            break
    for prop in snapshot.get("property_values") or snapshot.get("propertyValues") or []:
        if not isinstance(prop, dict):
            continue
        name = str(prop.get("property_name") or prop.get("propertyName") or "").strip()
        value = str(prop.get("property_value") or prop.get("propertyValue") or "").strip()
        if name and value and not any(item["name"] == name for item in result):
            result.append({"name": name[:40], "value": value[:160]})
        if len(result) >= 10:
            break
    return result


class ProductComparisonService:
    async def compare(self, user_id: str, product_ids: list[Any] | None) -> ToolInvokeResult:
        selected = normalize_comparison_ids(product_ids)
        mission = await shopping_mission_service.load(user_id)
        candidates = {
            str(item.get("productId")): item
            for item in (mission or {}).get("candidateProducts") or []
            if isinstance(item, dict) and item.get("productId")
        }
        allowed = set(await shopping_mission_service.allowed_candidate_ids(user_id))
        denied = [product_id for product_id in selected if product_id not in allowed]
        if denied:
            raise ComparisonCandidateDenied(
                "只能比较当前或近期推荐列表中的商品，请重新选择"
            )

        snapshots = await product_service._load_products_by_ids(selected)
        by_product_id = {
            str(item.get("product_id") or item.get("productId") or ""): item
            for item in snapshots
        }
        if any(product_id not in by_product_id for product_id in selected):
            raise ComparisonSnapshotMissing("部分商品已下架或不存在，无法生成可靠比较")
        annotated = await product_decision_feature_service.annotate_candidates(
            [by_product_id[product_id] for product_id in selected]
        )
        try:
            offers = await final_offer_snapshot_service.build(user_id, annotated)
        except OfferSnapshotUnavailable as exc:
            raise ComparisonSnapshotMissing("当前无法核验实时价格、库存或优惠") from exc
        offers_by_id = {
            str(item.get("product_id") or item.get("productId") or ""): item
            for item in offers
        }
        if any(product_id not in offers_by_id for product_id in selected):
            raise ComparisonSnapshotMissing("部分商品当前不可购买，无法生成可靠比较")

        products: list[dict[str, Any]] = []
        dimensions: list[str] = ["到手估算", "库存", "适合谁", "取舍"]
        names: list[str] = []
        for product_id in selected:
            snapshot = offers_by_id[product_id]
            product_name = str(snapshot.get("product_name") or snapshot.get("productName") or product_id)
            names.append(product_name)
            properties = _flatten_properties(snapshot)
            for prop in properties:
                if prop["name"] not in dimensions:
                    dimensions.append(prop["name"])
            observed = candidates.get(product_id) or {}
            observed_price = _number(
                observed.get("estimatedPayable") or observed.get("minPrice")
            )
            current_price = _number(
                snapshot.get("estimated_payable")
                if snapshot.get("estimated_payable") is not None
                else snapshot.get("estimatedPayable")
            )
            recommendation = observed.get("recommendation")
            features = [
                {
                    "key": str(item.get("key") or ""),
                    "value": str(item.get("value") or ""),
                    "evidence": item.get("evidence"),
                }
                for item in snapshot.get("decisionFeatures") or []
                if isinstance(item, dict) and str(item.get("reviewStatus") or "") == "VERIFIED"
            ][:8]
            for feature in features:
                label = f"已核验:{feature['key']}"
                if feature["value"] and label not in dimensions:
                    dimensions.append(label)
            products.append(
                {
                    "productId": product_id,
                    "productName": product_name,
                    "cover": snapshot.get("cover"),
                    "minPrice": current_price,
                    "basePrice": _number(snapshot.get("base_price") or snapshot.get("basePrice")),
                    "estimatedPayable": current_price,
                    "offerSnapshotId": snapshot.get("offer_snapshot_id") or snapshot.get("offerSnapshotId"),
                    "skuKey": snapshot.get("sku_key") or snapshot.get("skuKey"),
                    "coupon": snapshot.get("coupon"),
                    "couponStatus": snapshot.get("coupon_status") or snapshot.get("couponStatus"),
                    "quoteExpiresAt": snapshot.get("quote_expires_at") or snapshot.get("quoteExpiresAt"),
                    "deliveryPromise": snapshot.get("delivery_promise") or snapshot.get("deliveryPromise"),
                    "totalStock": snapshot.get("total_stock") or snapshot.get("totalStock"),
                    "availability": _availability(snapshot),
                    "properties": properties,
                    "verifiedFeatures": features,
                    "recommendation": recommendation if isinstance(recommendation, dict) else None,
                    "sourceMessageId": observed.get("sourceMessageId"),
                    "observedMinPrice": observed_price,
                    "priceChanged": (
                        observed_price is not None
                        and current_price is not None
                        and observed_price != current_price
                    ),
                }
            )

        card = {
            "type": PRODUCT_COMPARISON_TYPE,
            "snapshotType": "REAL_TIME",
            "offerContract": "FINAL_OFFER_SNAPSHOT",
            "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "dimensions": dimensions[:12],
            "products": products,
        }
        return ToolInvokeResult(
            content=f"【商品比较】已刷新并比较 {len(products)} 个商品的实时信息。",
            biz_type="product_comparison",
            biz_data=json.dumps(selected, ensure_ascii=False),
            assistant_cards=json.dumps(card, ensure_ascii=False),
            product_ids=selected,
            product_names=names,
        )


product_comparison_service = ProductComparisonService()
