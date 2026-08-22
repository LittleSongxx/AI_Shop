from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import httpx

from evaluation.core.contracts import PreflightError, ValidationError
from evaluation.core.io import (
    EVALUATION_ROOT,
    atomic_write_json,
    canonical_json_bytes,
    load_json,
    sha256_bytes,
    utc_now,
)

PRODUCT_INDEX = "aishop-index"
CATALOG_FIXTURES_ROOT = EVALUATION_ROOT / "fixtures"
CATALOG_FIXTURE_PATH = CATALOG_FIXTURES_ROOT / "product-catalog.v2.json"
_SOURCE_FIELDS = (
    "productId",
    "productName",
    "productDesc",
    "minPrice",
    "maxPrice",
    "categoryId",
    "brand",
    "status",
)


def _normalize_hit(hit: dict[str, Any]) -> dict[str, Any]:
    source = hit.get("_source") or {}
    return {
        "productId": str(source.get("productId") or hit.get("_id") or ""),
        "productName": str(source.get("productName") or ""),
        "productDesc": str(source.get("productDesc") or ""),
        "minPrice": source.get("minPrice"),
        "maxPrice": source.get("maxPrice"),
        "categoryId": str(source.get("categoryId") or ""),
        **({"brand": source["brand"]} if source.get("brand") is not None else {}),
        **({"status": source["status"]} if source.get("status") is not None else {}),
    }


async def fetch_product_catalog() -> list[dict[str, Any]]:
    from app.config.settings import get_settings

    settings = get_settings()
    host = settings.es_hosts.split(",")[0].strip().rstrip("/")
    auth = (settings.es_username, settings.es_password) if settings.es_username.strip() else None
    async with httpx.AsyncClient(
        auth=auth,
        verify=settings.es_verify_ssl,
        trust_env=False,
    ) as client:
        response = await client.post(
            f"{host}/{PRODUCT_INDEX}/_search",
            json={
                "size": 1000,
                "query": {"match_all": {}},
                "_source": list(_SOURCE_FIELDS),
            },
            timeout=30,
        )
        response.raise_for_status()
    hits = response.json().get("hits", {}).get("hits", [])
    indexed_products = sorted(
        (_normalize_hit(hit) for hit in hits),
        key=lambda item: item["productId"],
    )
    if not indexed_products or any(
        not item["productId"] or not item["productName"] for item in indexed_products
    ):
        raise PreflightError("product catalog is empty or contains incomplete products")
    from app.services.java_internal_client import java_internal_client

    snapshot = await java_internal_client.snapshot_batch(
        [str(item["productId"]) for item in indexed_products]
    )
    return _merge_authoritative_availability(indexed_products, snapshot)


def _merge_authoritative_availability(
    indexed_products: list[dict[str, Any]],
    snapshot: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("products"), list):
        raise PreflightError("authoritative Java product snapshot is unavailable")
    raw_stocks = snapshot.get("total_stocks")
    if not isinstance(raw_stocks, dict):
        raise PreflightError("authoritative Java stock snapshot is unavailable")
    authoritative: dict[str, dict[str, Any]] = {}
    for item in snapshot["products"]:
        if not isinstance(item, dict):
            continue
        product_id = str(item.get("product_id") or item.get("productId") or "")
        if product_id in authoritative:
            raise PreflightError(
                f"duplicate authoritative product snapshot for {product_id or '<blank>'}"
            )
        authoritative[product_id] = item
    total_stocks = {str(key): value for key, value in raw_stocks.items()}
    expected_ids = {str(item["productId"]) for item in indexed_products}
    if expected_ids - authoritative.keys() or expected_ids - total_stocks.keys():
        raise PreflightError("authoritative Java product snapshot is incomplete")

    merged: list[dict[str, Any]] = []
    for indexed in indexed_products:
        product = dict(indexed)
        product_id = str(product["productId"])
        java_product = authoritative[product_id]
        status = java_product.get("status")
        raw_stock = total_stocks[product_id]
        try:
            stock = float(raw_stock)
        except (TypeError, ValueError) as exc:
            raise PreflightError(
                f"invalid authoritative stock for product {product_id}"
            ) from exc
        if isinstance(raw_stock, bool) or not math.isfinite(stock) or stock < 0 or not stock.is_integer():
            raise PreflightError(f"invalid authoritative stock for product {product_id}")
        in_stock = stock > 0
        if product.get("status") is not None:
            product["indexStatus"] = product["status"]
        product.update(
            {
                "status": status,
                "inStock": in_stock,
                "authoritativeAvailable": str(status) == "1" and in_stock,
                "availabilitySource": "JAVA_GATEWAY",
            }
        )
        merged.append(product)
    return merged


def product_catalog_sha256(products: list[dict[str, Any]]) -> str:
    return sha256_bytes(canonical_json_bytes(products))


async def write_catalog_fixture(path: Path = CATALOG_FIXTURE_PATH) -> dict[str, Any]:
    products = await fetch_product_catalog()
    value = {
        "schemaVersion": "aishop-evaluation-product-catalog/v2",
        "capturedAt": utc_now(),
        "index": PRODUCT_INDEX,
        "productCount": len(products),
        "canonicalSha256": product_catalog_sha256(products),
        "products": products,
    }
    atomic_write_json(path, value)
    return value


def _catalog_fixture_paths() -> list[Path]:
    return sorted(CATALOG_FIXTURES_ROOT.glob("product-catalog.v*.json"))


def load_catalog_fixture(
    path: Path | None = None,
    *,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    candidates = [path] if path is not None else _catalog_fixture_paths()
    if expected_sha256:
        candidates = [
            candidate
            for candidate in candidates
            if candidate is not None
            and candidate.is_file()
            and load_json(candidate).get("canonicalSha256") == expected_sha256
        ]
    if not candidates:
        suffix = f" with hash {expected_sha256}" if expected_sha256 else ""
        raise ValidationError(f"missing product catalog fixture{suffix}")
    path = candidates[-1]
    if not path.is_file():
        raise ValidationError(f"missing product catalog fixture: {path}")
    value = load_json(path)
    if value.get("schemaVersion") not in {
        "aishop-evaluation-product-catalog/v1",
        "aishop-evaluation-product-catalog/v2",
    }:
        raise ValidationError("invalid product catalog fixture schema")
    products = value.get("products")
    if not isinstance(products, list) or not products:
        raise ValidationError("product catalog fixture is empty")
    digest = product_catalog_sha256(products)
    if value.get("canonicalSha256") != digest:
        raise ValidationError("product catalog fixture hash is invalid")
    if int(value.get("productCount") or 0) != len(products):
        raise ValidationError("product catalog fixture count is invalid")
    if value.get("schemaVersion") == "aishop-evaluation-product-catalog/v2" and any(
        not isinstance(product.get("authoritativeAvailable"), bool)
        or product.get("availabilitySource") != "JAVA_GATEWAY"
        for product in products
    ):
        raise ValidationError("v2 product catalog lacks authoritative availability evidence")
    return value


async def verify_live_catalog() -> dict[str, Any]:
    fixture = load_catalog_fixture()
    live = await fetch_product_catalog()
    live_sha256 = product_catalog_sha256(live)
    expected = str(fixture["canonicalSha256"])
    if live_sha256 != expected:
        raise PreflightError(
            f"live product catalog changed: expected {expected}, got {live_sha256}"
        )
    return {
        "index": PRODUCT_INDEX,
        "productCount": len(live),
        "canonicalSha256": live_sha256,
        "matchesFixture": True,
    }
