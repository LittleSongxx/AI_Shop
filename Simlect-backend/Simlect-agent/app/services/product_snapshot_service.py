import json
from collections import OrderedDict
from decimal import Decimal
from typing import Any

import structlog

from app.services.java_internal_client import java_internal_client
from app.services.redis_service import redis_service
from app.utils.biz_payload import first_cover

logger = structlog.get_logger()

DESC_MAX_LEN = 3000
PRODUCT_STATUS_ON_SALE = 1

def _truncate_text(text: str | None, max_len: int = DESC_MAX_LEN) -> str | None:
    if not text:
        return text
    if len(text) <= max_len:
        return text
    return text[:max_len] + "…"

def _json_default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

class ProductSnapshotService:

    async def build_snapshot_json(self, user_id: str, product_id: str) -> str | None:

        try:
            detail = await java_internal_client.get_product_detail(product_id)
            if detail:
                return self._detail_to_snapshot_json(user_id, detail)

            batch = await java_internal_client.snapshot_batch([product_id])
            if not batch:
                return None
            products = batch.get("products") or []
            product = next(
                (p for p in products if str(p.get("product_id")) == str(product_id)),
                products[0] if products else None,
            )
            if not product:
                return None
            prop_rows = [
                pv for pv in (batch.get("property_values") or [])
                if str(pv.get("product_id")) == str(product_id)
            ]
            sku_rows = [
                s for s in (batch.get("skus") or [])
                if str(s.get("product_id")) == str(product_id)
            ]
            return self._assemble_snapshot_json(user_id, product, prop_rows, sku_rows)
        except Exception as e:
            logger.error("product_snapshot_http_failed", product_id=product_id, error=str(e))
            return None

    def _detail_to_snapshot_json(self, user_id: str, detail: dict) -> str:

        if detail.get("skus") is not None or detail.get("properties") is not None:
            snapshot = OrderedDict()
            snapshot["userId"] = user_id
            snapshot["productId"] = str(detail.get("product_id") or detail.get("productId") or "")
            snapshot["productName"] = detail.get("product_name") or detail.get("productName")
            snapshot["productDesc"] = _truncate_text(
                detail.get("product_desc") or detail.get("productDesc")
            )
            min_p = detail.get("min_price") if detail.get("min_price") is not None else detail.get("minPrice")
            max_p = detail.get("max_price") if detail.get("max_price") is not None else detail.get("maxPrice")
            snapshot["minPrice"] = float(min_p) if min_p is not None else None
            snapshot["maxPrice"] = float(max_p) if max_p is not None else None
            snapshot["status"] = detail.get("status")
            snapshot["totalSale"] = detail.get("total_sale") if detail.get("total_sale") is not None else detail.get("totalSale")
            snapshot["cover"] = first_cover(detail.get("cover"))
            cat = detail.get("category_id") or detail.get("categoryId")
            snapshot["categoryId"] = str(cat) if cat else None
            skus = detail.get("skus") or []
            snapshot["skus"] = skus
            total_stock = detail.get("total_stock")
            if total_stock is None:
                total_stock = detail.get("totalStock")
            if total_stock is None:
                total_stock = sum(max(0, int(s.get("stock") or 0)) for s in skus if isinstance(s, dict))
            snapshot["totalStock"] = total_stock
            snapshot["inStock"] = bool(detail.get("in_stock") if detail.get("in_stock") is not None else detail.get("inStock", total_stock > 0))
            snapshot["properties"] = detail.get("properties") or []
            return json.dumps(snapshot, ensure_ascii=False, default=_json_default)

        return self._assemble_snapshot_json(
            user_id,
            detail,
            detail.get("property_values") or [],
            detail.get("sku_list") or detail.get("skus") or [],
        )

    def _assemble_snapshot_json(
        self,
        user_id: str,
        product: dict,
        prop_rows: list[dict],
        sku_rows: list[dict],
    ) -> str:
        properties = self._group_properties(prop_rows)
        skus = [
            {
                "propertyValueIds": r.get("property_value_ids") or r.get("propertyValueIds"),
                "price": float(r["price"]) if r.get("price") is not None else None,
                "stock": r.get("stock"),
            }
            for r in sku_rows
        ]
        total_stock = sum(max(0, int(s.get("stock") or 0)) for s in skus)

        snapshot: dict[str, Any] = OrderedDict()
        snapshot["userId"] = user_id
        snapshot["productId"] = str(product.get("product_id") or "")
        snapshot["productName"] = product.get("product_name")
        snapshot["productDesc"] = _truncate_text(product.get("product_desc"))
        snapshot["minPrice"] = float(product["min_price"]) if product.get("min_price") is not None else None
        snapshot["maxPrice"] = float(product["max_price"]) if product.get("max_price") is not None else None
        snapshot["status"] = product.get("status")
        snapshot["totalSale"] = product.get("total_sale")
        snapshot["cover"] = first_cover(product.get("cover"))
        snapshot["categoryId"] = str(product["category_id"]) if product.get("category_id") else None
        snapshot["skus"] = skus
        snapshot["totalStock"] = total_stock
        snapshot["inStock"] = total_stock > 0
        snapshot["properties"] = properties
        return json.dumps(snapshot, ensure_ascii=False, default=_json_default)

    def _group_properties(self, rows: list[dict]) -> list[dict]:
        prop_map: OrderedDict[str, dict] = OrderedDict()
        for row in rows or []:
            pid = row.get("property_id") or row.get("propertyId")
            if not pid:

                pid = row.get("property_name") or row.get("propertyName") or "_"
            if pid not in prop_map:
                prop_map[pid] = {
                    "propertyId": row.get("property_id") or row.get("propertyId") or pid,
                    "propertyName": row.get("property_name") or row.get("propertyName"),
                    "propertySort": row.get("property_sort") or row.get("propertySort"),
                    "coverType": row.get("cover_type") or row.get("coverType"),
                    "propertyValues": [],
                }
            prop_map[pid]["propertyValues"].append({
                "propertyValueId": row.get("property_value_id") or row.get("propertyValueId"),
                "propertyValue": row.get("property_value") or row.get("propertyValue"),
                "propertyCover": row.get("property_cover") or row.get("propertyCover"),
                "propertyRemark": row.get("property_remark") or row.get("propertyRemark"),
            })
        return list(prop_map.values())

    def build_fallback_json(self, user_id: str, card: dict) -> str:
        fallback = {
            "userId": user_id,
            "productId": card.get("productId"),
            "productName": card.get("productName"),
            "cover": card.get("cover"),
            "minPrice": card.get("minPrice"),
        }
        return json.dumps(fallback, ensure_ascii=False, default=_json_default)

    async def resolve_active_snapshot(self, user_id: str, card: dict | None) -> str | None:
        if card and card.get("productId"):
            snapshot = await self.build_snapshot_json(user_id, str(card["productId"]))
            if snapshot:
                await redis_service.save_consult_product(user_id, json.loads(snapshot))
                await redis_service.set_consult_active(user_id)
                return snapshot
            fallback = self.build_fallback_json(user_id, card)
            await redis_service.save_consult_product(user_id, json.loads(fallback))
            await redis_service.set_consult_active(user_id)
            return fallback
        cached = await redis_service.get_consult_product(user_id)
        if cached:
            normalized = self._normalize_snapshot_keys(cached)
            return json.dumps(normalized, ensure_ascii=False, default=_json_default)
        return None

    def _normalize_snapshot_keys(self, data: dict) -> dict:
        key_map = {
            "product_id": "productId",
            "product_name": "productName",
            "product_desc": "productDesc",
            "min_price": "minPrice",
            "max_price": "maxPrice",
            "total_sale": "totalSale",
            "total_stock": "totalStock",
            "in_stock": "inStock",
            "category_id": "categoryId",
            "user_id": "userId",
        }
        out: dict[str, Any] = {}
        for k, v in data.items():
            out[key_map.get(k, k)] = v
        return out

    async def ensure_consult_snapshot(self, user_id: str, product_id: str) -> None:
        snapshot = await self.build_snapshot_json(user_id, product_id)
        if snapshot:
            await redis_service.save_consult_product(user_id, json.loads(snapshot))
        await redis_service.set_consult_active(user_id)

product_snapshot_service = ProductSnapshotService()
