"""Authoritative, user-bound SKU offer snapshots for Agent recommendations."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog

from app.config.settings import get_settings
from app.db.pool import acquire
from app.services.java_internal_client import java_internal_client

logger = structlog.get_logger()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _get(mapping: dict[str, Any], *keys: str) -> Any:
    return next((mapping[key] for key in keys if key in mapping and mapping[key] is not None), None)


class OfferSnapshotUnavailable(RuntimeError):
    code = "OFFER_SNAPSHOT_UNAVAILABLE"


class FinalOfferSnapshotService:
    async def build(self, user_id: str, products: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not products:
            return []
        product_ids = [
            str(_get(product, "product_id", "productId") or "").strip()
            for product in products
        ]
        product_ids = [product_id for product_id in product_ids if product_id]
        if not product_ids:
            return []
        settings = get_settings()
        try:
            raw = await java_internal_client.offer_snapshot_batch(user_id, product_ids)
        except Exception as exc:
            logger.warning("offer_snapshot_java_unavailable", error=type(exc).__name__)
            raise OfferSnapshotUnavailable from exc
        if not isinstance(raw, dict) or not isinstance(raw.get("products"), list):
            raise OfferSnapshotUnavailable("invalid offer snapshot response")

        rows = [row for row in raw["products"] if isinstance(row, dict)]
        coupon_rows: list[dict[str, Any]] = []
        coupon_items = []
        for row in rows:
            selected = _get(row, "selected_sku", "selectedSku")
            if not isinstance(selected, dict):
                continue
            coupon_items.append(
                {
                    "productId": _get(row, "product_id", "productId"),
                    "categoryId": _get(row, "category_id", "categoryId"),
                    "skuKey": _get(selected, "property_value_id_hash", "propertyValueIdHash", "skuKey"),
                    "basePrice": _get(selected, "price", "basePrice"),
                }
            )
        if coupon_items:
            try:
                coupon_rows = await java_internal_client.estimate_single_sku_offers(
                    user_id, coupon_items
                )
            except Exception as exc:
                # Base price and stock remain authoritative; inability to inspect
                # coupons must never invent a discount or block the entire mall.
                logger.warning("offer_coupon_estimate_unavailable", error=type(exc).__name__)

        coupon_by_key = {
            str(_get(row, "offer_key", "offerKey") or ""): row
            for row in coupon_rows
            if isinstance(row, dict)
        }
        product_by_id = {
            str(_get(product, "product_id", "productId")): product for product in products
        }
        result: list[dict[str, Any]] = []
        expires_at = _now() + timedelta(seconds=settings.shopping_offer_ttl_seconds)
        for row in rows:
            product_id = str(_get(row, "product_id", "productId") or "")
            source = product_by_id.get(product_id, {})
            selected = _get(row, "selected_sku", "selectedSku")
            if not isinstance(selected, dict):
                continue
            status = _get(row, "status")
            in_stock = _get(row, "in_stock", "inStock")
            if str(status) != "1" or in_stock is False:
                continue
            sku_key = str(
                _get(selected, "property_value_id_hash", "propertyValueIdHash", "skuKey") or ""
            )
            base_price = _as_float(_get(selected, "price", "basePrice"))
            if not sku_key or base_price is None or base_price < 0:
                continue
            offer_key = f"{product_id}:{sku_key}"
            coupon = coupon_by_key.get(offer_key) or {}
            coupon_status = str(_get(coupon, "status") or "UNAVAILABLE")
            payable = _as_float(_get(coupon, "estimated_payable", "estimatedPayable"))
            if payable is None:
                payable = base_price
            snapshot_id = f"offer_{uuid.uuid4().hex}"
            offer = {
                "snapshotId": snapshot_id,
                "userId": user_id,
                "productId": product_id,
                "productName": _get(row, "product_name", "productName") or _get(source, "product_name", "productName"),
                "cover": _get(row, "cover") or _get(source, "cover"),
                "categoryId": _get(row, "category_id", "categoryId") or _get(source, "category_id", "categoryId"),
                "skuKey": sku_key,
                "skuProperties": _get(selected, "property_value_ids", "propertyValueIds"),
                "basePrice": base_price,
                "estimatedPayable": payable,
                "couponStatus": coupon_status,
                "coupon": {
                    "userCouponId": _get(coupon, "user_coupon_id", "userCouponId"),
                    "couponName": _get(coupon, "coupon_name", "couponName"),
                    "estimatedDiscount": _as_float(_get(coupon, "estimated_discount", "estimatedDiscount")),
                    "validEndTime": _get(coupon, "valid_end_time", "validEndTime"),
                }
                if coupon_status == "AVAILABLE"
                else None,
                "stock": _get(row, "stock") or _get(selected, "stock"),
                "inStock": True,
                "status": status,
                "quoteCapturedAt": _now().isoformat().replace("+00:00", "Z"),
                "quoteExpiresAt": expires_at.isoformat().replace("+00:00", "Z"),
                "deliveryPromise": _get(row, "delivery_promise", "deliveryPromise"),
                "factSources": ["JAVA_PRODUCT", "JAVA_STOCK", "JAVA_COUPON"],
            }
            await self._persist(snapshot_id, user_id, product_id, sku_key, offer, expires_at)
            card = dict(source)
            card.update(
                {
                    "product_id": product_id,
                    "product_name": offer["productName"],
                    "cover": offer["cover"],
                    "category_id": offer["categoryId"],
                    "status": status,
                    "in_stock": True,
                    "total_stock": _get(row, "total_stock", "totalStock"),
                    "min_price": payable,
                    "max_price": payable,
                    "base_price": base_price,
                    "estimated_payable": payable,
                    "coupon_status": coupon_status,
                    "coupon": offer["coupon"],
                    "offer_snapshot_id": snapshot_id,
                    "sku_key": sku_key,
                    "quote_expires_at": offer["quoteExpiresAt"],
                    "delivery_promise": offer["deliveryPromise"],
                }
            )
            result.append(card)
        return result

    async def _persist(
        self,
        snapshot_id: str,
        user_id: str,
        product_id: str,
        sku_key: str,
        offer: dict[str, Any],
        expires_at: datetime,
    ) -> None:
        try:
            async with acquire() as cur:
                await cur.execute(
                    """
                    INSERT INTO agent_final_offer_snapshot
                        (snapshot_id,user_id,product_id,sku_key,offer_json,expires_at,created_at)
                    VALUES (%s,%s,%s,%s,%s,%s,NOW(3))
                    """,
                    (
                        snapshot_id,
                        user_id,
                        product_id,
                        sku_key,
                        json.dumps(offer, ensure_ascii=False, default=str),
                        expires_at.replace(tzinfo=None),
                    ),
                )
        except Exception as exc:
            logger.warning("offer_snapshot_persist_failed", snapshot_id=snapshot_id, error=type(exc).__name__)

    async def get(self, user_id: str, snapshot_id: str) -> dict[str, Any] | None:
        if not user_id or not snapshot_id:
            return None
        try:
            async with acquire() as cur:
                await cur.execute(
                    "SELECT offer_json, expires_at FROM agent_final_offer_snapshot "
                    "WHERE user_id=%s AND snapshot_id=%s AND expires_at>NOW(3)",
                    (user_id, snapshot_id),
                )
                row = await cur.fetchone()
        except Exception:
            return None
        if not row:
            return None
        offer = row.get("offer_json")
        if isinstance(offer, str):
            try:
                offer = json.loads(offer)
            except json.JSONDecodeError:
                return None
        return offer if isinstance(offer, dict) else None


final_offer_snapshot_service = FinalOfferSnapshotService()
