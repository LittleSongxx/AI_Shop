from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from app.constants import PRODUCT_STATUS_ON_SALE
from app.services.product_snapshot_service import product_snapshot_service
from app.services.shopping_need_service import shopping_need_service
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
    if snapshot.get("inStock") is False:
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
    return result


class ProductComparisonService:
    async def compare(self, user_id: str, product_ids: list[Any] | None) -> ToolInvokeResult:
        selected = normalize_comparison_ids(product_ids)
        need = await shopping_need_service.load(user_id)
        candidates = {
            str(item.get("productId")): item
            for item in (need or {}).get("candidateProducts") or []
            if isinstance(item, dict) and item.get("productId")
        }
        allowed = set(await shopping_need_service.allowed_candidate_ids(user_id))
        denied = [product_id for product_id in selected if product_id not in allowed]
        if denied:
            raise ComparisonCandidateDenied(
                "只能比较当前或近期推荐列表中的商品，请重新选择"
            )

        raw_snapshots = await asyncio.gather(
            *[
                product_snapshot_service.build_snapshot_json(user_id, product_id)
                for product_id in selected
            ]
        )
        if any(snapshot is None for snapshot in raw_snapshots):
            raise ComparisonSnapshotMissing("部分商品已不存在，无法生成可靠比较")

        products: list[dict[str, Any]] = []
        dimensions: list[str] = ["价格", "库存"]
        names: list[str] = []
        for product_id, raw_snapshot in zip(selected, raw_snapshots):
            snapshot = json.loads(raw_snapshot or "{}")
            product_name = str(snapshot.get("productName") or product_id)
            names.append(product_name)
            properties = _flatten_properties(snapshot)
            for prop in properties:
                if prop["name"] not in dimensions:
                    dimensions.append(prop["name"])
            observed = candidates.get(product_id) or {}
            observed_price = _number(observed.get("minPrice"))
            current_price = _number(snapshot.get("minPrice"))
            products.append(
                {
                    "productId": product_id,
                    "productName": product_name,
                    "cover": snapshot.get("cover"),
                    "minPrice": current_price,
                    "maxPrice": _number(snapshot.get("maxPrice")),
                    "totalStock": snapshot.get("totalStock"),
                    "availability": _availability(snapshot),
                    "properties": properties,
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
