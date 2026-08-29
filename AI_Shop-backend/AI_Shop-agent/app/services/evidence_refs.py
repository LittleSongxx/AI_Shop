"""Small, server-owned evidence references for customer-service tool results.

The Agent must be able to distinguish a fact returned by a Java-owned service
from a model-generated statement.  These helpers deliberately keep only the
fields needed to audit an answer: object identity, the authoritative snapshot
boundary, the query/result status and a bounded set of public facts.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from app.utils.biz_payload import (
    PUBLIC_PRODUCT_RANKING_FIELDS,
    PUBLIC_PRODUCT_RECOMMENDATION_FIELDS,
)


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


_PRODUCT_QUERY_SCOPE_SCHEMA = "product-query-scope/v1"
_QUERY_SCOPE_MODEL_SURFACE_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_-]{1,79}$"
)
_QUERY_SCOPE_MODEL_TOKEN_RE = re.compile(r"^[a-z0-9]{2,80}$")
_QUERY_SCOPE_MAX_CHARS = 4000
_QUERY_SCOPE_MAX_ITEMS = 12
_QUERY_SCOPE_MAX_TERM_CHARS = 120


def _query_scope_values(
    value: Any,
    *,
    pattern: re.Pattern[str] | None = None,
) -> list[str] | None:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        return None
    result: list[str] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, str):
            return None
        item = " ".join(raw.strip().split())
        if (
            not item
            or len(item) > _QUERY_SCOPE_MAX_TERM_CHARS
            or any(ord(character) < 32 for character in item)
            or (pattern is not None and not pattern.fullmatch(item))
        ):
            return None
        identity = item.casefold()
        if identity in seen:
            continue
        seen.add(identity)
        result.append(item)
        if len(result) > _QUERY_SCOPE_MAX_ITEMS:
            return None
    return result


def _query_scope_number(value: Any) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("query scope budget must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("query scope budget must be numeric") from exc
    if not math.isfinite(number) or number < 0 or number > 1_000_000_000:
        raise ValueError("query scope budget is outside the supported range")
    return int(number) if number.is_integer() else number


def _product_query_scope(value: Any) -> dict[str, Any] | None:
    """Accept only the bounded query projection produced by the search parser."""

    if not isinstance(value, Mapping):
        return None
    if value.get("schemaVersion") != _PRODUCT_QUERY_SCOPE_SCHEMA:
        return None
    digest = str(value.get("querySha256") or "")
    query_chars = value.get("queryChars")
    comparison_required = value.get("comparisonRequired")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        return None
    if (
        isinstance(query_chars, bool)
        or not isinstance(query_chars, int)
        or not 0 <= query_chars <= _QUERY_SCOPE_MAX_CHARS
        or not isinstance(comparison_required, bool)
    ):
        return None

    requested_models = _query_scope_values(
        value.get("requestedModels"), pattern=_QUERY_SCOPE_MODEL_SURFACE_RE
    )
    model_tokens = _query_scope_values(
        value.get("modelTokens"), pattern=_QUERY_SCOPE_MODEL_TOKEN_RE
    )
    must_terms = _query_scope_values(value.get("mustTerms"))
    must_not_terms = _query_scope_values(value.get("mustNotTerms"))
    comparison_targets = _query_scope_values(value.get("comparisonTargets"))
    if any(
        items is None
        for items in (
            requested_models,
            model_tokens,
            must_terms,
            must_not_terms,
            comparison_targets,
        )
    ):
        return None
    assert requested_models is not None
    assert model_tokens is not None
    if {
        re.sub(r"[^a-z0-9]", "", item.casefold()) for item in requested_models
    } != set(model_tokens):
        return None
    try:
        budget_min = _query_scope_number(value.get("budgetMin"))
        budget_max = _query_scope_number(value.get("budgetMax"))
    except ValueError:
        return None
    if budget_min is not None and budget_max is not None and budget_min > budget_max:
        return None
    return {
        "schemaVersion": _PRODUCT_QUERY_SCOPE_SCHEMA,
        "querySha256": digest,
        "queryChars": query_chars,
        "requestedModels": requested_models,
        "modelTokens": model_tokens,
        "budgetMin": budget_min,
        "budgetMax": budget_max,
        "mustTerms": must_terms,
        "mustNotTerms": must_not_terms,
        "comparisonTargets": comparison_targets,
        "comparisonRequired": comparison_required,
    }


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


def _product_offer_claims(
    product: Mapping[str, Any],
    *,
    product_id: str,
    source: str,
    captured: str,
) -> list[dict[str, Any]]:
    """Bind every structured offer/ranking card fact to its server snapshot."""

    snapshot_id = _text(
        product.get("offer_snapshot_id") or product.get("offerSnapshotId"), limit=120
    )
    offer_subject = snapshot_id or product_id

    def claim(
        fact_path: str,
        value: Any,
        *,
        subject_type: str = "product",
        subject_id: str = product_id,
        claim_type: str = "DYNAMIC_FACT",
    ) -> dict[str, Any] | None:
        if value in (None, ""):
            return None
        if isinstance(value, (dict, list, tuple, set)):
            return None
        return {
            "claimType": claim_type,
            "subjectType": subject_type,
            "subjectId": subject_id,
            "factPath": fact_path,
            "value": value,
            "sourceType": source,
            "sourceId": subject_id,
            "capturedAt": captured,
        }

    price = _number(
        product.get("estimated_payable")
        if product.get("estimated_payable") is not None
        else product.get("estimatedPayable")
        if product.get("estimatedPayable") is not None
        else product.get("min_price")
        if product.get("min_price") is not None
        else product.get("minPrice")
    )
    stock = _number(
        product.get("total_stock")
        if product.get("total_stock") is not None
        else product.get("totalStock")
    )
    status = product.get("status")
    in_stock = product.get("in_stock", product.get("inStock"))
    if status is not None and str(status) != "1":
        availability = "UNAVAILABLE"
    elif in_stock is False:
        availability = "OUT_OF_STOCK"
    elif status is not None:
        availability = "ON_SALE"
    else:
        availability = None
    rows = [
        claim(
            "product.productName",
            _text(product.get("product_name") or product.get("productName")),
        ),
        claim("product.cover", _text(product.get("cover"))),
        claim(
            "offer.offerSnapshotId",
            snapshot_id,
            subject_type="offer_snapshot",
            subject_id=offer_subject,
            claim_type="OFFER_SNAPSHOT_FACT",
        ),
        claim(
            "offer.skuKey",
            _text(product.get("sku_key") or product.get("skuKey"), limit=160),
            subject_type="offer_snapshot",
            subject_id=offer_subject,
            claim_type="OFFER_SNAPSHOT_FACT",
        ),
        claim(
            "offer.price",
            price,
            subject_type="offer_snapshot",
            subject_id=offer_subject,
            claim_type="OFFER_SNAPSHOT_FACT",
        ),
        claim(
            "offer.stock",
            stock,
            subject_type="offer_snapshot",
            subject_id=offer_subject,
            claim_type="OFFER_SNAPSHOT_FACT",
        ),
        claim(
            "offer.inStock",
            in_stock,
            subject_type="offer_snapshot",
            subject_id=offer_subject,
            claim_type="OFFER_SNAPSHOT_FACT",
        ),
        claim(
            "offer.availability",
            availability,
            subject_type="offer_snapshot",
            subject_id=offer_subject,
            claim_type="OFFER_SNAPSHOT_FACT",
        ),
    ]
    offer_fields = (
        ("offer.minPrice", "min_price", "minPrice"),
        ("offer.maxPrice", "max_price", "maxPrice"),
        ("offer.basePrice", "base_price", "basePrice"),
        ("offer.estimatedPayable", "estimated_payable", "estimatedPayable"),
        ("offer.couponStatus", "coupon_status", "couponStatus"),
        ("offer.quoteExpiresAt", "quote_expires_at", "quoteExpiresAt"),
        ("offer.deliveryPromise", "delivery_promise", "deliveryPromise"),
    )
    for fact_path, snake, camel in offer_fields:
        value = product.get(snake) if product.get(snake) is not None else product.get(camel)
        if fact_path in {
            "offer.minPrice",
            "offer.maxPrice",
            "offer.basePrice",
            "offer.estimatedPayable",
        }:
            value = _number(value)
        else:
            value = _text(value)
        rows.append(
            claim(
                fact_path,
                value,
                subject_type="offer_snapshot",
                subject_id=offer_subject,
                claim_type="OFFER_SNAPSHOT_FACT",
            )
        )
    coupon = product.get("coupon")
    if isinstance(coupon, Mapping):
        for fact_path, snake, camel in (
            ("offer.coupon.couponName", "coupon_name", "couponName"),
            ("offer.coupon.estimatedDiscount", "estimated_discount", "estimatedDiscount"),
            ("offer.coupon.validEndTime", "valid_end_time", "validEndTime"),
        ):
            value = coupon.get(snake) if coupon.get(snake) is not None else coupon.get(camel)
            value = _number(value) if fact_path.endswith("estimatedDiscount") else _text(value)
            rows.append(
                claim(
                    fact_path,
                    value,
                    subject_type="offer_snapshot",
                    subject_id=offer_subject,
                    claim_type="OFFER_SNAPSHOT_FACT",
                )
            )
    ranking_subject = _text(
        product.get("ranking_decision_id") or product.get("rankingDecisionId"), limit=120
    ) or product_id
    rows.extend(
        (
            claim(
                "ranking.rankingDecisionId",
                _text(
                    product.get("ranking_decision_id")
                    or product.get("rankingDecisionId"),
                    limit=120,
                ),
                subject_type="product_ranking",
                subject_id=ranking_subject,
                claim_type="RANKING_DECISION_FACT",
            ),
            claim(
                "ranking.position",
                _number(product.get("position")),
                subject_type="product_ranking",
                subject_id=ranking_subject,
                claim_type="RANKING_DECISION_FACT",
            ),
        )
    )
    ranking = product.get("ranking")
    if isinstance(ranking, Mapping):
        for key in sorted(PUBLIC_PRODUCT_RANKING_FIELDS):
            rows.append(
                claim(
                    f"ranking.{key}",
                    ranking.get(key),
                    subject_type="product_ranking",
                    subject_id=ranking_subject,
                    claim_type="RANKING_DECISION_FACT",
                )
            )
    reason = product.get("_recommend_reason") or product.get("recommend_reason")
    rows.append(
        claim(
            "recommendation.reason",
            _text(reason, limit=80),
            subject_type="product_ranking",
            subject_id=ranking_subject,
            claim_type="RECOMMENDATION_DECISION_FACT",
        )
    )
    recommendation = product.get("recommendation")
    if isinstance(recommendation, Mapping):
        for key in sorted(PUBLIC_PRODUCT_RECOMMENDATION_FIELDS - {"evidence"}):
            rows.append(
                claim(
                    f"recommendation.{key}",
                    recommendation.get(key),
                    subject_type="product_ranking",
                    subject_id=ranking_subject,
                    claim_type="RECOMMENDATION_DECISION_FACT",
                )
            )
    operation_recommended = product.get("operation_recommended") or product.get(
        "operationRecommended"
    )
    rows.extend(
        (
            claim(
                "recommendation.operationRecommended",
                True if operation_recommended else None,
                subject_type="product_ranking",
                subject_id=ranking_subject,
                claim_type="RECOMMENDATION_DECISION_FACT",
            ),
            claim(
                "recommendation.commercialDisclosure",
                _text(product.get("commercialDisclosure"), limit=48)
                or ("运营推荐" if operation_recommended else None),
                subject_type="product_ranking",
                subject_id=ranking_subject,
                claim_type="RECOMMENDATION_DECISION_FACT",
            ),
        )
    )
    for feature in product.get("decisionFeatures") or []:
        if not isinstance(feature, Mapping) or feature.get("reviewStatus") != "VERIFIED":
            continue
        key = _text(feature.get("key"), limit=64)
        value = _text(feature.get("value"), limit=160)
        if not key or not value:
            continue
        path_key = re.sub(r"[\s.]+", "_", key).strip("_") or "feature"
        rows.append(
            claim(
                f"recommendation.verifiedFeature.{path_key[:64]}",
                value,
                subject_type="product_ranking",
                subject_id=ranking_subject,
                claim_type="VERIFIED_DECISION_FEATURE",
            )
        )
    for card_key, snake, camel in (
        ("retrievalMode", "retrieval_mode", "retrievalMode"),
        ("matchType", "match_type", "matchType"),
        ("subjectLabel", "subject_label", "subjectLabel"),
        ("recallSource", "recall_source", "recallSource"),
        ("modelVersion", "model_version", "modelVersion"),
    ):
        value = product.get(snake) if product.get(snake) is not None else product.get(camel)
        rows.append(
            claim(
                f"retrieval.{card_key}",
                _text(value, limit=100),
                subject_type="product_retrieval",
                subject_id=product_id,
                claim_type="RETRIEVAL_DECISION_FACT",
            )
        )
    return [row for row in rows if row is not None]


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
        offer_claims = _product_offer_claims(
            product,
            product_id=product_id,
            source=source,
            captured=now,
        )
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
            "availability": next(
                (
                    row["value"]
                    for row in offer_claims
                    if row.get("factPath") == "offer.availability"
                ),
                None,
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
        claims.extend(offer_claims)
        if claims:
            ref["claims"] = claims[:96]
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
    required_qualifier_ids = [
        value
        for raw in payload.get("requiredQualifierIds") or []
        if (value := _text(raw, limit=120))
    ]
    if not excluded_brands and not excluded_terms and not required_qualifier_ids:
        return None
    violating_ids = [
        value
        for raw in payload.get("violatingReturnedProductIds") or []
        if (value := _text(raw, limit=120))
    ][:30]
    unverified_qualifier_ids = [
        value
        for raw in payload.get("unverifiedRequiredQualifierProductIds") or []
        if (value := _text(raw, limit=120))
    ][:30]
    try:
        candidate_count = max(0, int(payload.get("returnedCandidateCount") or 0))
    except (TypeError, ValueError):
        candidate_count = 0
    try:
        unverified_qualifier_count = max(
            0,
            int(payload.get("unverifiedRequiredQualifierCandidateCount") or 0),
        )
    except (TypeError, ValueError):
        unverified_qualifier_count = len(unverified_qualifier_ids)
    now = captured or captured_at()
    return {
        "type": "product_search_constraint",
        "id": _ref_id(
            "product-search-constraint",
            request_id,
            ",".join(excluded_brands),
            ",".join(excluded_terms),
            ",".join(required_qualifier_ids),
            candidate_count,
        ),
        "excludedBrands": excluded_brands,
        "excludedTerms": excluded_terms,
        "requiredQualifierIds": required_qualifier_ids,
        "returnedCandidateCount": candidate_count,
        "violatingReturnedProductIds": violating_ids,
        "unverifiedRequiredQualifierProductIds": unverified_qualifier_ids,
        "unverifiedRequiredQualifierCandidateCount": unverified_qualifier_count,
        "returnedCandidatesSatisfyExclusions": bool(
            payload.get("returnedCandidatesSatisfyExclusions")
        ),
        "returnedCandidatesSatisfyRequiredQualifiers": bool(
            payload.get("returnedCandidatesSatisfyRequiredQualifiers")
        ),
        "requiredQualifierEvidenceSource": (
            _text(payload.get("requiredQualifierEvidenceSource"), limit=120)
            or "JAVA_PRODUCT_SNAPSHOT"
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
    query_scope: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Represent a bounded no-result response without hiding uncertainty."""

    scope = _product_query_scope(query_scope)
    scope_material = (
        json.dumps(scope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if scope is not None
        else ""
    )
    ref_id = (
        _ref_id("product-search", query, result_source, request_id, scope_material)
        if scope is not None
        else _ref_id("product-search", query, result_source, request_id)
    )
    ref = {
        "type": "product",
        "id": ref_id,
        "query": _text(query, limit=500),
        "queryScope": scope,
        "matched": False,
        "resultSource": _text(result_source, limit=80),
        "authoritative": bool(authoritative),
        "catalogAbsenceClaim": False,
        "source": "JAVA_GATEWAY",
        "requestId": _text(request_id, limit=120),
        "capturedAt": captured or captured_at(),
    }
    return {key: value for key, value in ref.items() if value is not None}


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
    snapshot_version = _text(
        decision.get("snapshot_version") or decision.get("snapshotVersion"),
        limit=120,
    )
    snapshot_etag = _text(
        decision.get("snapshot_etag")
        or decision.get("snapshotEtag")
        or decision.get("snapshotETag"),
        limit=160,
    )
    snapshot_hash = _text(
        decision.get("snapshot_hash") or decision.get("snapshotHash"),
        limit=128,
    )
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
    # Snapshot metadata is a non-secret proof boundary: the action token and
    # raw order payload stay out of evidence refs, while a reviewer can still
    # match the capability declaration to the confirmation ledger.
    if snapshot_version:
        ref["snapshotVersion"] = snapshot_version
        ref["claims"][0]["snapshotVersion"] = snapshot_version
    if snapshot_etag:
        ref["snapshotEtag"] = snapshot_etag
        ref["claims"][0]["snapshotEtag"] = snapshot_etag
    if snapshot_hash:
        ref["snapshotHash"] = snapshot_hash
        ref["claims"][0]["snapshotHash"] = snapshot_hash
    return {key: value for key, value in ref.items() if value not in (None, "")}


def pending_action_ref(
    pending: Mapping[str, Any],
    *,
    source: str = "AGENT_PENDING_ACTION_STORE",
    captured: str | None = None,
) -> dict[str, Any] | None:
    """Project a persisted proposal without exposing its action credential."""

    if not isinstance(pending, Mapping):
        return None
    action = _text(pending.get("actionType"), limit=64)
    args_fingerprint = _text(pending.get("argsFingerprint"), limit=64)
    business_key = _text(pending.get("businessKey"), limit=500)
    if not action or not args_fingerprint or not business_key:
        return None
    try:
        status_code = int(pending.get("status", 0))
    except (TypeError, ValueError):
        return None
    status_names = {
        0: "PENDING",
        1: "CONFIRMED",
        2: "CANCELLED",
        3: "EXECUTING",
        4: "FAILED",
        5: "EXPIRED",
        6: "INCONCLUSIVE",
        7: "MANUAL_REVIEW",
    }
    status = (
        _text(pending.get("statusName"), limit=32) or status_names.get(status_code) or ""
    ).upper()
    if status not in set(status_names.values()):
        return None
    now = captured or captured_at()
    proposal_id = _ref_id(
        "action-proposal",
        action,
        hashlib.sha256(business_key.encode("utf-8")).hexdigest(),
        args_fingerprint,
        pending.get("createTime") or pending.get("createdAt"),
    )
    requires_confirmation = status == "PENDING"
    claim = {
        "claimType": "ACTION_PROPOSAL_STATE",
        "subjectType": "action_proposal",
        "subjectId": proposal_id,
        "actionType": action,
        "status": status,
        "proposalPersisted": True,
        "requiresUserConfirmation": requires_confirmation,
        "sourceType": source,
        "sourceId": proposal_id,
        "capturedAt": now,
    }
    if requires_confirmation:
        claim["effectExecuted"] = False
    ref = {
        "type": "action_proposal",
        "id": proposal_id,
        "actionType": action,
        "status": status,
        "proposalPersisted": True,
        "requiresUserConfirmation": requires_confirmation,
        "effectExecuted": False if requires_confirmation else None,
        "argsFingerprint": args_fingerprint,
        "source": source,
        "capturedAt": now,
        "claims": [claim],
    }
    try:
        params = pending.get("paramsJson") or pending.get("params_json") or "{}"
        params = json.loads(params) if isinstance(params, str) else params
    except (TypeError, json.JSONDecodeError):
        params = {}
    snapshot = params.get("actionSnapshot") if isinstance(params, dict) else None
    if isinstance(snapshot, Mapping):
        snapshot_version = _text(
            snapshot.get("version") or snapshot.get("snapshotVersion"), limit=120
        )
        snapshot_etag = _text(
            snapshot.get("etag")
            or snapshot.get("snapshotEtag")
            or snapshot.get("snapshotETag"),
            limit=160,
        )
        snapshot_hash = _text(
            snapshot.get("hash") or snapshot.get("snapshotHash"), limit=128
        )
        if snapshot_version:
            ref["snapshotVersion"] = snapshot_version
            claim["snapshotVersion"] = snapshot_version
        if snapshot_etag:
            ref["snapshotEtag"] = snapshot_etag
            claim["snapshotEtag"] = snapshot_etag
        if snapshot_hash:
            ref["snapshotHash"] = snapshot_hash
            claim["snapshotHash"] = snapshot_hash
    return {key: value for key, value in ref.items() if value is not None}


def support_case_ref(
    case: Mapping[str, Any],
    *,
    source: str = "AGENT_SUPPORT_CASE_STORE",
    captured: str | None = None,
) -> dict[str, Any] | None:
    """Project a persisted support case as bounded creation evidence."""

    if not isinstance(case, Mapping):
        return None
    case_no = _text(case.get("caseNo") or case.get("case_no"), limit=120)
    status = _text(case.get("status"), limit=32)
    if not case_no or not status:
        return None
    now = captured or captured_at()
    category = _text(case.get("category"), limit=64)
    ref_id = _ref_id("support-case", case_no, status, category)
    forced_handoff = bool(case.get("forcedHandoff") or case.get("forced_handoff"))
    claims = [
        {
            "claimType": "SUPPORT_CASE_STATE",
            "subjectType": "support_case",
            "subjectId": case_no,
            "factPath": "supportCase.status",
            "value": status,
            "sourceType": source,
            "sourceId": case_no,
            "capturedAt": now,
        }
    ]
    if forced_handoff:
        claims.append(
            {
                "claimType": "SUPPORT_CASE_STATE",
                "subjectType": "support_case",
                "subjectId": case_no,
                "factPath": "supportCase.forcedHandoff",
                "value": True,
                "sourceType": source,
                "sourceId": case_no,
                "capturedAt": now,
            }
        )
    ref = {
        "type": "support_case",
        "id": ref_id,
        "caseNo": case_no,
        "status": status,
        "category": category,
        "forcedHandoff": forced_handoff,
        "source": source,
        "capturedAt": now,
        "claims": claims,
    }
    return {key: value for key, value in ref.items() if value is not None}


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
