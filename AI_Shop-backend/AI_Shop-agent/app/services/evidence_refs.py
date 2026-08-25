"""Small, server-owned evidence references for customer-service tool results.

The Agent must be able to distinguish a fact returned by a Java-owned service
from a model-generated statement.  These helpers deliberately keep only the
fields needed to audit an answer: object identity, the authoritative snapshot
boundary, the query/result status and a bounded set of public facts.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping


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


def _property_text(value: Any, *, limit: int = 240) -> str | None:
    """Return a bounded scalar property value without serializing objects."""

    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if not isinstance(value, str):
        return None
    text = " ".join(value.strip().split())
    return text[:limit] if text else None


def _property_pairs(
    value: Any,
    *,
    inherited_name: str | None = None,
    depth: int = 0,
) -> Iterable[tuple[str, str]]:
    """Read only structured catalog properties, never arbitrary product prose."""

    if depth > 3:
        return
    if isinstance(value, Mapping):
        raw_name = (
            value.get("property_name")
            or value.get("propertyName")
            or value.get("name")
        )
        name = _property_text(raw_name, limit=80) or inherited_name
        scalar = next(
            (
                _property_text(value.get(key))
                for key in ("property_value", "propertyValue", "value")
                if _property_text(value.get(key)) is not None
            ),
            None,
        )
        if name and scalar:
            yield name, scalar
        nested_keys = ("property_values", "propertyValues", "values")
        nested_found = False
        for key in nested_keys:
            nested = value.get(key)
            if nested is None:
                continue
            nested_found = True
            yield from _property_pairs(
                nested,
                inherited_name=name,
                depth=depth + 1,
            )
        # Some gateway payloads use {"颜色": "黑色"}. Accept that bounded
        # schema, but only when the object was not already a named property.
        if not name and not nested_found:
            reserved = {
                "property_id",
                "propertyId",
                "property_value_id",
                "propertyValueId",
                "property_cover",
                "propertyCover",
                "property_remark",
                "propertyRemark",
            }
            for key, item in value.items():
                if key in reserved:
                    continue
                key_text = _property_text(key, limit=80)
                item_text = _property_text(item)
                if key_text and item_text:
                    yield key_text, item_text
        return
    if isinstance(value, (list, tuple)):
        for item in value[:80]:
            yield from _property_pairs(
                item,
                inherited_name=inherited_name,
                depth=depth + 1,
            )
        return
    scalar = _property_text(value)
    if inherited_name and scalar:
        yield inherited_name, scalar


def _product_property_claims(
    product: Mapping[str, Any],
    *,
    product_id: str,
    source: str,
    captured: str,
) -> list[dict[str, Any]]:
    """Build identity-bound claims for compact, structured catalog fields."""

    pairs: list[tuple[str, str]] = []
    brand = _property_text(product.get("brand"), limit=120)
    if brand:
        pairs.append(("品牌", brand))
    for key in ("property_values", "propertyValues", "properties"):
        pairs.extend(_property_pairs(product.get(key)))

    claims: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw_name, raw_value in pairs:
        name = _property_text(raw_name, limit=80)
        value = _property_text(raw_value)
        if not name or not value:
            continue
        identity = (name.casefold(), value.casefold())
        if identity in seen:
            continue
        seen.add(identity)
        path_name = re.sub(r"[\s.]+", "_", name).strip("_") or "attribute"
        claims.append(
            {
                "claimType": "PRODUCT_PROPERTY",
                "subjectType": "product",
                "subjectId": product_id,
                "factPath": f"product.property.{path_name[:80]}",
                "propertyName": name,
                "value": value,
                "sourceType": source,
                "sourceId": product_id,
                "capturedAt": captured,
            }
        )
    return claims[:48]


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
        claims = _product_property_claims(
            product,
            product_id=product_id,
            source=source,
            captured=now,
        )
        if claims:
            ref["claims"] = claims
        # Keep the ref compact and deterministic; absent values are not evidence.
        refs.append({key: value for key, value in ref.items() if value not in (None, "")})
    return refs[:30]


def product_search_constraint_ref(
    evidence: Mapping[str, Any] | None,
    *,
    request_id: str | None = None,
    source: str = "JAVA_GATEWAY",
    captured: str | None = None,
) -> dict[str, Any] | None:
    """Persist a returned-candidate exclusion audit in business source refs.

    The assertion is intentionally scoped to candidates returned by this search.
    It is not a claim about the complete catalogue.
    """

    payload = evidence or {}
    excluded_brands = [
        value
        for raw in payload.get("excludedBrands") or []
        if (value := _text(raw, limit=120))
    ]
    excluded_terms = [
        value
        for raw in payload.get("excludedTerms") or []
        if (value := _text(raw, limit=120))
    ]
    if not excluded_brands and not excluded_terms:
        return None
    violating_ids = [
        value
        for raw in payload.get("violatingReturnedProductIds") or []
        if (value := _text(raw, limit=120))
    ][:30]
    try:
        candidate_count = max(0, int(payload.get("returnedCandidateCount") or 0))
    except (TypeError, ValueError):
        candidate_count = 0
    now = captured or captured_at()
    return {
        "type": "product_search_constraint",
        "id": _ref_id(
            "product-search-constraint",
            request_id,
            ",".join(excluded_brands),
            ",".join(excluded_terms),
            candidate_count,
        ),
        "excludedBrands": excluded_brands,
        "excludedTerms": excluded_terms,
        "returnedCandidateCount": candidate_count,
        "violatingReturnedProductIds": violating_ids,
        "returnedCandidatesSatisfyExclusions": bool(
            payload.get("returnedCandidatesSatisfyExclusions")
        ),
        "catalogAbsenceClaim": False,
        "source": source,
        "requestId": _text(request_id, limit=120),
        "capturedAt": now,
    }


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
    """Build bounded, field-level evidence for Java-owned order snapshots.

    An order reference is deliberately more precise than a generic ``source``
    marker.  A response or UI card may only expose an order field when a claim
    binds its value to the order/order-item returned by the Java service in
    this request.  This prevents a model-derived product name, specification,
    or amount from being laundered through an otherwise valid order ID.

    The public projection below intentionally excludes user IDs, payment
    channel/order IDs, and other fields that are neither needed by the Agent
    answer nor appropriate to persist in its evidence envelope.
    """

    def claim(
        *,
        subject_type: str,
        subject_id: str,
        fact_path: str,
        value: Any,
        source_id: str,
    ) -> dict[str, Any] | None:
        if value in (None, ""):
            return None
        return {
            "claimType": "DYNAMIC_FACT",
            "subjectType": subject_type,
            "subjectId": subject_id,
            "factPath": fact_path,
            "value": value,
            "sourceType": source,
            "sourceId": source_id,
            "capturedAt": now,
        }

    now = captured or captured_at()
    refs: list[dict[str, Any]] = []
    for order in orders:
        if not isinstance(order, dict):
            continue
        order_id = _text(order.get("order_id") or order.get("orderId"), limit=120)
        if not order_id:
            continue
        status = (
            order.get("order_status")
            if order.get("order_status") is not None
            else order.get("orderStatus")
        )
        amount = _number(
            order.get("_orderAmount")
            if order.get("_orderAmount") is not None
            else order.get("amount")
            if order.get("amount") is not None
            else order.get("orderAmount")
        )
        order_time = _text(
            order.get("create_time")
            or order.get("createTime")
            or order.get("order_time")
            or order.get("orderTime"),
            limit=64,
        )
        pay_scene = _text(order.get("pay_scene") or order.get("payScene"), limit=120)
        pay_channel = _text(
            order.get("pay_channel") or order.get("payChannel"), limit=120
        )
        subject = _text(order.get("subject"), limit=500)
        comment_status = _number(
            order.get("comment_status")
            if order.get("comment_status") is not None
            else order.get("commentStatus")
        )
        status_name = _text(
            order.get("order_status_name") or order.get("orderStatusName"),
            limit=120,
        )
        claims = [
            claim(
                subject_type="order",
                subject_id=order_id,
                fact_path="order.orderId",
                value=order_id,
                source_id=order_id,
            ),
            claim(
                subject_type="order",
                subject_id=order_id,
                fact_path="order.orderStatus",
                value=status,
                source_id=order_id,
            ),
            claim(
                subject_type="order",
                subject_id=order_id,
                fact_path="order.orderStatusName",
                value=status_name,
                source_id=order_id,
            ),
            claim(
                subject_type="order",
                subject_id=order_id,
                fact_path="order.amount",
                value=amount,
                source_id=order_id,
            ),
            claim(
                subject_type="order",
                subject_id=order_id,
                fact_path="order.orderTime",
                value=order_time,
                source_id=order_id,
            ),
            claim(
                subject_type="order",
                subject_id=order_id,
                fact_path="order.payScene",
                value=pay_scene,
                source_id=order_id,
            ),
            claim(
                subject_type="order",
                subject_id=order_id,
                fact_path="order.payChannel",
                value=pay_channel,
                source_id=order_id,
            ),
            claim(
                subject_type="order",
                subject_id=order_id,
                fact_path="order.subject",
                value=subject,
                source_id=order_id,
            ),
            claim(
                subject_type="order",
                subject_id=order_id,
                fact_path="order.commentStatus",
                value=comment_status,
                source_id=order_id,
            ),
        ]
        ref: dict[str, Any] = {
            "type": "order",
            "id": order_id,
            "orderId": order_id,
            "matched": True,
            "orderStatus": status,
            "orderStatusName": status_name,
            "amount": amount,
            "orderTime": order_time,
            "payScene": pay_scene,
            "payChannel": pay_channel,
            "subject": subject,
            "commentStatus": comment_status,
            "source": source,
            "schemaVersion": "order-fact/v2",
            "authorityBoundary": "AUTHENTICATED_OWNER_JAVA_ORDER_SERVICE",
            "capturedAt": now,
        }
        items = (
            order.get("items")
            or order.get("order_item_list")
            or order.get("orderItems")
            or order.get("orderItemList")
        )
        # Resolver candidates are intentionally flattened for matching and
        # may carry one public order-item directly instead of an ``items``
        # list. Rehydrate only that bounded projection so its fields still get
        # field-level claims without copying arbitrary model state.
        if not isinstance(items, list) and (
            order.get("orderItemId") or order.get("order_item_id")
        ):
            items = [{
                "orderId": order_id,
                "orderItemId": order.get("orderItemId") or order.get("order_item_id"),
                "productId": order.get("productId") or order.get("product_id"),
                "productName": order.get("productName") or order.get("product_name"),
                "propertyInfo": order.get("propertyInfo") or order.get("property_info"),
                "itemAmount": order.get("_itemAmount"),
                "orderItemStatus": (
                    order.get("orderItemStatus")
                    if order.get("orderItemStatus") is not None
                    else order.get("order_item_status")
                ),
            }]
        if isinstance(items, list):
            item_ids: list[str] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                item_order_id = _text(
                    item.get("order_id") or item.get("orderId"), limit=120
                )
                # A malformed or stale nested item must never become evidence
                # for the surrounding order card.
                if item_order_id and item_order_id != order_id:
                    continue
                item_id = _text(
                    item.get("order_item_id") or item.get("orderItemId"), limit=120
                )
                if not item_id:
                    continue
                item_ids.append(item_id)
                product_id = _text(
                    item.get("product_id") or item.get("productId"), limit=120
                )
                product_name = _text(
                    item.get("product_name") or item.get("productName"), limit=500
                )
                property_info = _text(
                    item.get("property_info") or item.get("propertyInfo"), limit=500
                )
                item_amount = _number(
                    item.get("item_amount")
                    if item.get("item_amount") is not None
                    else item.get("itemAmount")
                )
                buy_count = _number(
                    item.get("buy_count")
                    if item.get("buy_count") is not None
                    else item.get("buyCount")
                )
                item_status = (
                    item.get("order_item_status")
                    if item.get("order_item_status") is not None
                    else item.get("orderItemStatus")
                )
                cover = _text(item.get("cover"), limit=500)
                property_value_id_hash = _text(
                    item.get("property_value_id_hash")
                    or item.get("propertyValueIdHash"),
                    limit=160,
                )
                claims.extend(
                    claim_row
                    for claim_row in (
                        claim(
                            subject_type="order_item",
                            subject_id=item_id,
                            fact_path="order_item.orderId",
                            value=order_id,
                            source_id=order_id,
                        ),
                        claim(
                            subject_type="order_item",
                            subject_id=item_id,
                            fact_path="order_item.orderItemId",
                            value=item_id,
                            source_id=order_id,
                        ),
                        claim(
                            subject_type="order_item",
                            subject_id=item_id,
                            fact_path="order_item.productId",
                            value=product_id,
                            source_id=order_id,
                        ),
                        claim(
                            subject_type="order_item",
                            subject_id=item_id,
                            fact_path="order_item.productName",
                            value=product_name,
                            source_id=order_id,
                        ),
                        claim(
                            subject_type="order_item",
                            subject_id=item_id,
                            fact_path="order_item.propertyInfo",
                            value=property_info,
                            source_id=order_id,
                        ),
                        claim(
                            subject_type="order_item",
                            subject_id=item_id,
                            fact_path="order_item.itemAmount",
                            value=item_amount,
                            source_id=order_id,
                        ),
                        claim(
                            subject_type="order_item",
                            subject_id=item_id,
                            fact_path="order_item.buyCount",
                            value=buy_count,
                            source_id=order_id,
                        ),
                        claim(
                            subject_type="order_item",
                            subject_id=item_id,
                            fact_path="order_item.orderItemStatus",
                            value=item_status,
                            source_id=order_id,
                        ),
                        claim(
                            subject_type="order_item",
                            subject_id=item_id,
                            fact_path="order_item.cover",
                            value=cover,
                            source_id=order_id,
                        ),
                        claim(
                            subject_type="order_item",
                            subject_id=item_id,
                            fact_path="order_item.propertyValueIdHash",
                            value=property_value_id_hash,
                            source_id=order_id,
                        ),
                    )
                    if claim_row is not None
                )
            item_ids = [item_id for item_id in item_ids if item_id]
            if item_ids:
                ref["orderItemIds"] = item_ids[:20]
        public_claims = [row for row in claims if row is not None]
        if public_claims:
            ref["claims"] = public_claims[:220]
        refs.append({key: value for key, value in ref.items() if value not in (None, "")})
    return refs[:30]


def action_capability_ref(
    decision: dict[str, Any],
    *,
    source: str = "JAVA_ORDER_SERVICE",
    captured: str | None = None,
) -> dict[str, Any] | None:
    """Convert a server-signed action decision into an auditable business ref.

    This accepts only the bounded decision shape returned by the Java internal
    endpoint.  It deliberately does not accept a free-form reason/message, so
    retrieved or model text cannot be promoted into an operation capability.
    """

    if not isinstance(decision, dict):
        return None
    action = _text(decision.get("action"), limit=64)
    order_id = _text(decision.get("order_id") or decision.get("orderId"), limit=120)
    outcome = _text(decision.get("decision"), limit=64)
    capability_version = _text(
        decision.get("capability_version") or decision.get("capabilityVersion"),
        limit=120,
    )
    evaluated_at = _text(
        decision.get("evaluated_at") or decision.get("evaluatedAt"), limit=64
    )
    if not action or not order_id or outcome not in {
        "ALLOWED",
        "DENIED",
        "MANUAL_REVIEW",
        "UNAVAILABLE",
    } or not capability_version or not evaluated_at:
        return None
    now = captured or evaluated_at
    item_id = _text(decision.get("order_item_id") or decision.get("orderItemId"), limit=120)
    ref: dict[str, Any] = {
        "type": "action_capability",
        "id": _ref_id(
            "action-capability",
            action,
            order_id,
            item_id,
            outcome,
            decision.get("evaluated_at") or decision.get("evaluatedAt"),
        ),
        "action": action,
        "orderId": order_id,
        "orderItemId": item_id,
        "decision": outcome,
        "reasonCode": _text(decision.get("reason_code") or decision.get("reasonCode"), limit=120),
        "capabilityVersion": capability_version,
        "evaluatedAt": now,
        "source": source,
        "claims": [
            {
                "claimType": "ACTION_CAPABILITY_DECISION",
                "subjectType": "order",
                "subjectId": order_id,
                "orderItemId": item_id,
                "action": action,
                "decision": outcome,
                "sourceType": source,
                "sourceId": order_id,
                "capturedAt": now,
            }
        ],
    }
    return {key: value for key, value in ref.items() if value not in (None, "")}


def after_sales_eligibility_ref(
    decision: dict[str, Any],
    *,
    source: str = "AGENT_AFTER_SALES_POLICY_ENGINE",
) -> dict[str, Any] | None:
    """Project a persisted, versioned refund/return decision as evidence."""

    if not isinstance(decision, dict):
        return None
    decision_id = _text(decision.get("decisionId") or decision.get("decision_id"), limit=160)
    action = _text(decision.get("action"), limit=64)
    order_id = _text(decision.get("orderId") or decision.get("order_id"), limit=120)
    outcome = _text(decision.get("decision"), limit=64)
    if not decision_id or not action or not order_id or outcome not in {
        "ELIGIBLE",
        "INELIGIBLE",
        "NEEDS_EVIDENCE",
        "POLICY_UNAVAILABLE",
        "CONFLICT",
    }:
        return None
    evaluated_at = _text(
        decision.get("evaluatedAt") or decision.get("evaluated_at"), limit=64
    )
    policy_id = _text(decision.get("policyId") or decision.get("policy_id"), limit=160)
    policy_version = _text(
        decision.get("policyVersion") or decision.get("policy_version"),
        limit=120,
    )
    # A refund result is only useful as auditable eligibility evidence when it
    # identifies the published policy version and the evaluation instant.
    # Otherwise a stale or hand-written decision could be mistaken for a
    # current policy-engine result.
    if not policy_id or not policy_version or not evaluated_at:
        return None
    ref = {
        "type": "after_sales_eligibility",
        "id": decision_id,
        "decisionId": decision_id,
        "decision": outcome,
        "action": action,
        "orderId": order_id,
        "orderItemId": _text(
            decision.get("orderItemId") or decision.get("order_item_id"), limit=120
        ),
        "policyId": policy_id,
        "policyVersion": policy_version,
        "evaluatedAt": evaluated_at,
        "source": source,
        "claims": [
            {
                "claimType": "AFTER_SALES_ELIGIBILITY_DECISION",
                "subjectType": "order",
                "subjectId": order_id,
                "orderItemId": _text(
                    decision.get("orderItemId") or decision.get("order_item_id"),
                    limit=120,
                ),
                "action": action,
                "decision": outcome,
                "decisionId": decision_id,
                "sourceType": source,
                "sourceId": decision_id,
                "capturedAt": evaluated_at or captured_at(),
            }
        ],
    }
    return {key: value for key, value in ref.items() if value not in (None, "")}


def order_card_fields_with_claims(
    candidate: dict[str, Any],
    source_refs: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Return only order-card fields covered by the matching snapshot claims.

    This is intentionally deterministic and value-sensitive.  A claim for an
    order ID alone cannot justify showing a product name from a different
    order/item, and an item must explicitly claim membership of the card's
    order before any item field is surfaced.
    """

    if not isinstance(candidate, dict):
        return {}
    order_id = _text(candidate.get("orderId"), limit=120)
    if not order_id:
        return {}
    item_id = _text(candidate.get("orderItemId"), limit=120)
    claims = [
        claim
        for ref in source_refs
        if isinstance(ref, dict)
        for claim in ref.get("claims") or []
        if isinstance(claim, dict)
    ]

    def has(
        subject_type: str,
        subject_id: str,
        fact_path: str,
        value: Any,
    ) -> bool:
        return any(
            claim.get("claimType") == "DYNAMIC_FACT"
            and claim.get("subjectType") == subject_type
            and str(claim.get("subjectId") or "") == subject_id
            and claim.get("factPath") == fact_path
            and claim.get("value") == value
            for claim in claims
        )

    if not has("order", order_id, "order.orderId", order_id):
        return {}
    target_type = str(candidate.get("targetType") or "")
    target_id = _text(candidate.get("targetId"), limit=120)
    if item_id and not (
        has("order_item", item_id, "order_item.orderItemId", item_id)
        and has("order_item", item_id, "order_item.orderId", order_id)
    ):
        # Never downgrade a mismatched item card to an order card. That would
        # hide the identity error while still presenting a concrete target.
        return {}

    result: dict[str, Any] = {}
    if target_type == "ORDER" and target_id == order_id:
        result.update({"targetType": target_type, "targetId": target_id})
    elif target_type == "ORDER_ITEM" and item_id and target_id == item_id:
        result.update({"targetType": target_type, "targetId": target_id})
    else:
        return {}
    result["orderId"] = order_id

    order_fields = {
        "orderStatus": "order.orderStatus",
        "orderTime": "order.orderTime",
        "payScene": "order.payScene",
        "payChannel": "order.payChannel",
        "subject": "order.subject",
        "commentStatus": "order.commentStatus",
    }
    for key, path in order_fields.items():
        value = candidate.get(key)
        if value not in (None, "") and has("order", order_id, path, value):
            result[key] = value
    # Status display text is a deterministic presentation of the claimed raw
    # status.  It is allowed only when that raw status is still covered.
    if (
        candidate.get("orderStatusName") not in (None, "")
        and candidate.get("orderStatus") not in (None, "")
        and has("order", order_id, "order.orderStatus", candidate.get("orderStatus"))
    ):
        result["orderStatusName"] = candidate["orderStatusName"]

    if item_id:
        result["orderItemId"] = item_id
        item_fields = {
            "productId": "order_item.productId",
            "productName": "order_item.productName",
            "propertyInfo": "order_item.propertyInfo",
            "orderItemStatus": "order_item.orderItemStatus",
            "cover": "order_item.cover",
            "propertyValueIdHash": "order_item.propertyValueIdHash",
            "buyCount": "order_item.buyCount",
        }
        for key, path in item_fields.items():
            value = candidate.get(key)
            if value not in (None, "") and has("order_item", item_id, path, value):
                result[key] = value
        amount_path = str(candidate.get("_amountFactPath") or "order_item.itemAmount")
        amount_subject = "order" if amount_path == "order.amount" else "order_item"
        amount_id = order_id if amount_subject == "order" else item_id
        if candidate.get("amount") not in (None, "") and has(
            amount_subject, amount_id, amount_path, candidate.get("amount")
        ):
            result["amount"] = candidate["amount"]
    elif candidate.get("amount") not in (None, "") and has(
        "order", order_id, "order.amount", candidate.get("amount")
    ):
        result["amount"] = candidate["amount"]
    return result


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
