"""Small, server-owned evidence references for customer-service tool results.

The Agent must be able to distinguish a fact returned by a Java-owned service
from a model-generated statement.  These helpers deliberately keep only the
fields needed to audit an answer: object identity, the authoritative snapshot
boundary, the query/result status and a bounded set of public facts.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Iterable


def _text(value: Any, *, limit: int = 500) -> str | None:
    text = str(value or "").strip()
    return text[:limit] if text else None


def _number(value: Any) -> int | float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


def captured_at() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _ref_id(prefix: str, *parts: Any) -> str:
    material = "\0".join(str(part or "") for part in parts)
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}:{digest}"


def product_refs(
    products: Iterable[dict[str, Any]],
    *,
    request_id: str | None = None,
    source: str = "JAVA_GATEWAY",
    captured: str | None = None,
) -> list[dict[str, Any]]:
    """Build one ref per returned product/offer snapshot."""

    now = captured or captured_at()
    refs: list[dict[str, Any]] = []
    for product in products:
        if not isinstance(product, dict):
            continue
        product_id = _text(product.get("product_id") or product.get("productId"), limit=120)
        if not product_id:
            continue
        snapshot_id = _text(product.get("offer_snapshot_id") or product.get("offerSnapshotId"), limit=120)
        sku_key = _text(product.get("sku_key") or product.get("skuKey"), limit=160)
        ref: dict[str, Any] = {
            "type": "product",
            "id": product_id,
            "productId": product_id,
            "productName": _text(product.get("product_name") or product.get("productName")),
            "offerSnapshotId": snapshot_id,
            "skuKey": sku_key,
            "price": _number(
                product.get("estimated_payable")
                if product.get("estimated_payable") is not None
                else product.get("estimatedPayable")
                if product.get("estimatedPayable") is not None
                else product.get("min_price")
                if product.get("min_price") is not None
                else product.get("minPrice")
            ),
            "stock": _number(
                product.get("total_stock")
                if product.get("total_stock") is not None
                else product.get("totalStock")
            ),
            "availability": (
                "ON_SALE"
                if str(product.get("status") or "1") == "1"
                and product.get("in_stock", product.get("inStock", True)) is not False
                else "UNAVAILABLE"
            ),
            "source": source,
            "requestId": _text(request_id, limit=120),
            "capturedAt": now,
        }
        # Keep the ref compact and deterministic; absent values are not evidence.
        refs.append({key: value for key, value in ref.items() if value not in (None, "")})
    return refs[:30]


def product_no_result_ref(
    query: str,
    *,
    result_source: str,
    request_id: str | None = None,
    authoritative: bool = False,
    captured: str | None = None,
) -> dict[str, Any]:
    """Represent a bounded no-result response without hiding uncertainty."""

    return {
        "type": "product",
        "id": _ref_id("product-search", query, result_source, request_id),
        "query": _text(query, limit=500),
        "matched": False,
        "resultSource": _text(result_source, limit=80),
        "authoritative": bool(authoritative),
        "source": "JAVA_GATEWAY",
        "requestId": _text(request_id, limit=120),
        "capturedAt": captured or captured_at(),
    }


def order_refs(
    orders: Iterable[dict[str, Any]],
    *,
    source: str = "JAVA_ORDER_SERVICE",
    captured: str | None = None,
) -> list[dict[str, Any]]:
    now = captured or captured_at()
    refs: list[dict[str, Any]] = []
    for order in orders:
        if not isinstance(order, dict):
            continue
        order_id = _text(order.get("order_id") or order.get("orderId"), limit=120)
        if not order_id:
            continue
        ref: dict[str, Any] = {
            "type": "order",
            "id": order_id,
            "orderId": order_id,
            "matched": True,
            "orderStatus": order.get("order_status") if order.get("order_status") is not None else order.get("orderStatus"),
            "orderStatusName": _text(order.get("order_status_name") or order.get("orderStatusName")),
            "amount": _number(order.get("amount") or order.get("orderAmount")),
            "orderTime": _text(order.get("create_time") or order.get("createTime") or order.get("orderTime"), limit=64),
            "source": source,
            "capturedAt": now,
        }
        items = (
            order.get("items")
            or order.get("order_item_list")
            or order.get("orderItems")
            or order.get("orderItemList")
        )
        if isinstance(items, list):
            item_ids = [
                _text(item.get("order_item_id") or item.get("orderItemId"), limit=120)
                for item in items
                if isinstance(item, dict)
            ]
            item_ids = [item_id for item_id in item_ids if item_id]
            if item_ids:
                ref["orderItemIds"] = item_ids[:20]
        refs.append({key: value for key, value in ref.items() if value not in (None, "")})
    return refs[:30]


def negative_lookup_ref(
    ref_type: str,
    *,
    query: dict[str, Any],
    source: str,
    matched: bool = False,
    authoritative: bool = True,
    captured: str | None = None,
) -> dict[str, Any]:
    """Create an auditable negative lookup; failures must call this with false."""

    query_text = "|".join(f"{key}={query[key]}" for key in sorted(query) if query[key] not in (None, ""))
    return {
        "type": ref_type,
        "id": _ref_id(f"{ref_type}-lookup", query_text, source),
        **{key: value for key, value in query.items() if value not in (None, "")},
        "matched": bool(matched),
        "authoritative": bool(authoritative),
        "source": source,
        "capturedAt": captured or captured_at(),
    }
