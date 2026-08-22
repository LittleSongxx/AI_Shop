"""SKU-aware evidence for hard product-term exclusions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.services.product_search_query import variant_exclusion_contracts


def _get(mapping: Mapping[str, Any], *keys: str) -> Any:
    return next(
        (mapping[key] for key in keys if key in mapping and mapping[key] is not None),
        None,
    )


def _normalized_terms(values: Sequence[Any]) -> tuple[str, ...]:
    result: list[str] = []
    for raw in values:
        value = str(raw or "").strip().casefold()
        if value and value not in result:
            result.append(value)
    return tuple(result)


def _property_rows(product: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = _get(product, "property_values", "propertyValues")
    return [item for item in raw or [] if isinstance(item, Mapping)]


def _sku_rows(product: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = product.get("skus")
    return [item for item in raw or [] if isinstance(item, Mapping)]


def _property_ids(raw: Any) -> tuple[str, ...]:
    if isinstance(raw, (list, tuple, set)):
        values = raw
    else:
        values = str(raw or "").split("-")
    return tuple(value for item in values if (value := str(item or "").strip()))


def _selected_sku_key(product: Mapping[str, Any]) -> str:
    direct = _get(product, "sku_key", "skuKey")
    if direct:
        return str(direct).strip()
    selected = _get(product, "selected_sku", "selectedSku")
    if isinstance(selected, Mapping):
        return str(
            _get(selected, "property_value_id_hash", "propertyValueIdHash", "skuKey")
            or ""
        ).strip()
    return ""


def sku_property_texts(
    product: Mapping[str, Any], *, selected_only: bool = False
) -> dict[str, str]:
    """Resolve SKU keys to Java-owned property names and values."""

    properties: dict[str, str] = {}
    for row in _property_rows(product):
        property_id = str(_get(row, "property_value_id", "propertyValueId") or "").strip()
        if not property_id:
            continue
        properties[property_id] = " ".join(
            str(value or "")
            for value in (
                _get(row, "property_name", "propertyName"),
                _get(row, "property_value", "propertyValue"),
            )
        ).casefold()

    selected_key = _selected_sku_key(product)
    result: dict[str, str] = {}
    for sku in _sku_rows(product):
        sku_key = str(
            _get(sku, "property_value_id_hash", "propertyValueIdHash", "skuKey") or ""
        ).strip()
        if not sku_key or (selected_only and selected_key and sku_key != selected_key):
            continue
        ids = _property_ids(_get(sku, "property_value_ids", "propertyValueIds"))
        if ids and all(property_id in properties for property_id in ids):
            result[sku_key] = " ".join(properties[property_id] for property_id in ids)

    if selected_only and selected_key and selected_key not in result:
        selected = _get(product, "selected_sku", "selectedSku")
        selected_ids = _get(product, "sku_properties", "skuProperties")
        if isinstance(selected, Mapping):
            selected_ids = _get(selected, "property_value_ids", "propertyValueIds") or selected_ids
        ids = _property_ids(selected_ids)
        if ids and all(property_id in properties for property_id in ids):
            result[selected_key] = " ".join(properties[property_id] for property_id in ids)
    return result


def _full_product_text(product: Mapping[str, Any]) -> str:
    values: list[Any] = [
        _get(product, "product_name", "productName"),
        _get(product, "product_desc", "productDesc", "description"),
        product.get("brand"),
        _get(product, "category", "categoryName", "category_name"),
        product.get("product_class"),
    ]
    for row in _property_rows(product):
        values.extend(
            (
                _get(row, "property_name", "propertyName"),
                _get(row, "property_value", "propertyValue"),
            )
        )
    return " ".join(str(value or "") for value in values).casefold()


def evaluate_excluded_terms(
    product: Mapping[str, Any],
    excluded_terms: Sequence[Any],
    *,
    selected_only: bool = False,
) -> dict[str, Any]:
    """Evaluate term exclusions without treating a mixed title as SKU truth.

    A title match remains blocking unless a configured contract has explicit
    alternative evidence on the selected (or candidate) SKU. This keeps the
    exception narrow and auditable.
    """

    terms = _normalized_terms(excluded_terms)
    text = _full_product_text(product)
    sku_text = sku_property_texts(product, selected_only=selected_only)
    eligible_keys = set(sku_text)
    restricted = False
    matched_terms: list[str] = []
    evidence_contracts: list[str] = []

    for term in terms:
        if term not in text:
            continue
        contracts = variant_exclusion_contracts(term)
        safe_keys: set[str] = set()
        for contract in contracts:
            alternatives = _normalized_terms(contract.get("requiredAlternativeTerms") or [])
            if not alternatives:
                continue
            safe_keys.update(
                sku_key
                for sku_key, value in sku_text.items()
                if term not in value and any(alternative in value for alternative in alternatives)
            )
            if safe_keys:
                evidence_contracts.append(str(contract["id"]))
        if safe_keys:
            eligible_keys.intersection_update(safe_keys)
            restricted = True
            if eligible_keys:
                continue
        matched_terms.append(term)

    return {
        "violates": bool(matched_terms),
        "matchedTerms": matched_terms,
        "eligibleSkuKeys": sorted(eligible_keys) if restricted and not matched_terms else [],
        "evidenceContracts": sorted(set(evidence_contracts)),
    }
