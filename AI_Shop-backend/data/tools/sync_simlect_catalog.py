#!/usr/bin/env python3
"""Mirror the authorized Simlect demo catalog for a local AI_Shop deployment.

The upstream repository does not contain the production product database or
upload directory. This tool obtains the public catalog through the same API as
the storefront, stores every referenced image locally, and emits an idempotent
SQL seed. Network work is deliberately single-threaded and resumable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


DATA_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_ROOT = DATA_ROOT / "simlect_catalog"
DETAILS_ROOT = SNAPSHOT_ROOT / "details"
CATALOG_JSON = SNAPSHOT_ROOT / "catalog.json"
VERSION_FILE = SNAPSHOT_ROOT / "VERSION"
ASSET_ROOT = DATA_ROOT / "simlect-assets"
ASSET_FILE_ROOT = ASSET_ROOT / "file"
ASSET_MANIFEST = ASSET_ROOT / "SOURCE_MANIFEST.json"
SQL_OUT = DATA_ROOT / "02_simlect_catalog_seed.sql"

DEFAULT_ORIGIN = "https://www.simlect.com"

# IDs created by the former 1,000-item synthetic demo seed.  They are removed
# only when no surviving catalog data references them.
LEGACY_SYNTHETIC_PARENT_CATEGORY_IDS = ("100", "200", "300", "400", "500", "600")
LEGACY_SYNTHETIC_CHILD_CATEGORY_IDS = (
    "101", "102", "103", "104", "105", "106",
    "201", "202", "203",
    "301", "302",
    "401", "402",
    "501", "502",
    "601", "602",
)
LEGACY_SYNTHETIC_PROPERTY_IDS = tuple(
    f"P{category_id}{suffix}"
    for category_id in LEGACY_SYNTHETIC_CHILD_CATEGORY_IDS
    for suffix in ("01", "02", "99")
)
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 SmartSelectMirror/1.0"
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_IMAGE_BYTES = 16 * 1024 * 1024
MIN_IMAGE_BYTES = 1_024
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
RESOURCE_PATTERN = re.compile(r"sourceName=([^\s)&\"'<>]+)", re.IGNORECASE)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def open_with_retry(request: urllib.request.Request, timeout: int = 45, attempts: int = 5) -> Any:
    for attempt in range(attempts):
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            retryable = exc.code == 429 or 500 <= exc.code <= 599
            if not retryable or attempt == attempts - 1:
                raise
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            try:
                delay = min(float(retry_after), 30.0) if retry_after else min(2.0 ** attempt, 12.0)
            except ValueError:
                delay = min(2.0 ** attempt, 12.0)
            print(f"remote HTTP {exc.code}; retrying in {delay:.1f}s", file=sys.stderr)
            time.sleep(delay)
        except urllib.error.URLError as exc:
            if attempt == attempts - 1:
                raise
            delay = min(2.0 ** attempt, 12.0)
            print(f"network error ({exc.reason}); retrying in {delay:.1f}s", file=sys.stderr)
            time.sleep(delay)
    raise AssertionError("unreachable")


def api_data(origin: str, path: str, form: dict[str, Any] | None = None) -> Any:
    body = None
    method = "GET"
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
    if form is not None:
        method = "POST"
        body = urllib.parse.urlencode(form).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = urllib.request.Request(
        urllib.parse.urljoin(origin.rstrip("/") + "/", path.lstrip("/")),
        data=body,
        headers=headers,
        method=method,
    )
    with open_with_retry(request) as response:
        raw = response.read(MAX_JSON_BYTES + 1)
    if len(raw) > MAX_JSON_BYTES:
        raise RuntimeError(f"API response is larger than {MAX_JSON_BYTES} bytes: {path}")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("code") != 200:
        raise RuntimeError(f"upstream API failed for {path}: {payload!r}")
    return payload.get("data")


def catalog_version(categories: list[dict[str, Any]], products: list[dict[str, Any]]) -> str:
    canonical = json.dumps(
        {"categories": categories, "products": products},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "simlect-mirror-" + hashlib.sha256(canonical).hexdigest()[:16]


def load_cached_detail(product_id: str) -> dict[str, Any] | None:
    path = DETAILS_ROOT / f"{product_id}.json"
    if not path.is_file():
        return None
    detail = json.loads(path.read_text(encoding="utf-8"))
    if str((detail.get("productInfo") or {}).get("productId") or "") != product_id:
        return None
    return detail


def fetch_metadata(origin: str, delay: float, refresh: bool, limit: int | None) -> dict[str, Any]:
    categories = api_data(origin, "/api/product/loadCategory")
    if not isinstance(categories, list):
        raise RuntimeError("loadCategory did not return a list")

    summaries: dict[str, dict[str, Any]] = {}
    commend = api_data(origin, "/api/product/loadCommendProduct")
    for product in commend if isinstance(commend, list) else []:
        summaries[str(product["productId"])] = product

    page_no = 1
    upstream_regular_count = 0
    while True:
        page = api_data(origin, "/api/product/loadProduct", {"pageNo": page_no})
        if not isinstance(page, dict):
            raise RuntimeError("loadProduct did not return a page object")
        upstream_regular_count = int(page.get("totalCount") or 0)
        for product in page.get("list") or []:
            summaries[str(product["productId"])] = product
        page_total = int(page.get("pageTotal") or 1)
        if page_no >= page_total:
            break
        page_no += 1
        time.sleep(delay)

    selected = list(summaries.values())
    if limit is not None:
        selected = selected[:limit]

    details: list[dict[str, Any]] = []
    DETAILS_ROOT.mkdir(parents=True, exist_ok=True)
    for index, summary in enumerate(selected, start=1):
        product_id = str(summary["productId"])
        detail = None if refresh else load_cached_detail(product_id)
        if detail is None:
            detail = api_data(origin, "/api/product/getProduct", {"productId": product_id})
            if not isinstance(detail, dict):
                raise RuntimeError(f"getProduct returned invalid data for {product_id}")
            atomic_write_json(DETAILS_ROOT / f"{product_id}.json", detail)
            time.sleep(delay)
        details.append(detail)
        print(f"metadata {index:02d}/{len(selected):02d}: {product_id}")

    version = catalog_version(categories, details)
    catalog = {
        "schemaVersion": 1,
        "catalogVersion": version,
        "sourceOrigin": origin.rstrip("/"),
        "sourceRegularProductCount": upstream_regular_count,
        "sourceCommendProductCount": len(commend) if isinstance(commend, list) else 0,
        "productCount": len(details),
        "syncedAt": utc_now(),
        "categories": categories,
        "products": details,
    }
    atomic_write_json(CATALOG_JSON, catalog)
    VERSION_FILE.write_text(version + "\n", encoding="utf-8")
    print(f"wrote {len(details)} products to {CATALOG_JSON}")
    return catalog


def load_catalog() -> dict[str, Any]:
    if not CATALOG_JSON.is_file():
        raise RuntimeError(f"missing {CATALOG_JSON}; run with --fetch-metadata first")
    return json.loads(CATALOG_JSON.read_text(encoding="utf-8"))


def safe_resource_path(value: Any) -> str:
    path_text = urllib.parse.unquote(str(value or "")).strip().replace("\\", "/")
    pure = PurePosixPath(path_text)
    if (
        not path_text
        or pure.is_absolute()
        or ".." in pure.parts
        or pure.suffix.lower() not in IMAGE_SUFFIXES
    ):
        raise ValueError(f"unsafe or unsupported image path: {value!r}")
    return pure.as_posix()


def collect_asset_references(catalog: dict[str, Any]) -> dict[str, dict[str, set[str]]]:
    references: dict[str, dict[str, set[str]]] = {}

    def add(path_value: Any, product_id: str, role: str) -> None:
        if path_value is None or str(path_value).strip() == "":
            return
        source_name = safe_resource_path(path_value)
        entry = references.setdefault(source_name, {"productIds": set(), "roles": set()})
        entry["productIds"].add(product_id)
        entry["roles"].add(role)

    for detail in catalog.get("products") or []:
        product = detail.get("productInfo") or {}
        product_id = str(product.get("productId") or "")
        for cover in str(product.get("cover") or "").split(","):
            add(cover, product_id, "gallery")
        for match in RESOURCE_PATTERN.finditer(str(product.get("productDesc") or "")):
            add(match.group(1), product_id, "description")
        for prop in detail.get("productPropertyList") or []:
            for value in prop.get("propertyValues") or []:
                add(value.get("propertyCover"), product_id, "property")
    return references


def looks_like_image(data: bytes) -> bool:
    return (
        data.startswith(b"\x89PNG\r\n\x1a\n")
        or data.startswith(b"\xff\xd8\xff")
        or data.startswith((b"GIF87a", b"GIF89a"))
        or (data.startswith(b"RIFF") and data[8:12] == b"WEBP")
        or data.startswith(b"BM")
    )


def read_local_image(path: Path) -> bytes | None:
    if not path.is_file():
        return None
    size = path.stat().st_size
    if size < MIN_IMAGE_BYTES or size > MAX_IMAGE_BYTES:
        return None
    data = path.read_bytes()
    return data if looks_like_image(data) else None


def download_resource(origin: str, source_name: str) -> tuple[bytes, str, str]:
    query = urllib.parse.urlencode({"sourceName": source_name})
    source_url = f"{origin.rstrip('/')}/api/file/getResource?{query}"
    request = urllib.request.Request(
        source_url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        },
    )
    with open_with_retry(request) as response:
        content_type = response.headers.get_content_type().lower()
        data = response.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise RuntimeError(f"image is larger than {MAX_IMAGE_BYTES} bytes: {source_name}")
    if len(data) < MIN_IMAGE_BYTES or not content_type.startswith("image/") or not looks_like_image(data):
        raise RuntimeError(
            f"invalid image response for {source_name}: type={content_type}, bytes={len(data)}"
        )
    return data, content_type, source_url


def load_asset_manifest(origin: str, version: str) -> dict[str, Any]:
    if ASSET_MANIFEST.is_file():
        return json.loads(ASSET_MANIFEST.read_text(encoding="utf-8"))
    return {
        "schemaVersion": 1,
        "catalogVersion": version,
        "sourceOrigin": origin,
        "sourceAccess": "Mirrored with deployer-confirmed authorization.",
        "assets": [],
    }


def save_asset_manifest(manifest: dict[str, Any]) -> None:
    manifest["updatedAt"] = utc_now()
    atomic_write_json(ASSET_MANIFEST, manifest)


def fetch_assets(catalog: dict[str, Any], delay: float, refresh: bool) -> dict[str, Any]:
    origin = str(catalog["sourceOrigin"])
    version = str(catalog["catalogVersion"])
    references = collect_asset_references(catalog)
    manifest = load_asset_manifest(origin, version)
    records = {
        str(record.get("sourceName")): record
        for record in manifest.get("assets") or []
        if record.get("sourceName") in references
    }

    total = len(references)
    downloaded = 0
    for index, source_name in enumerate(sorted(references), start=1):
        target = ASSET_FILE_ROOT / source_name
        data = None if refresh else read_local_image(target)
        source_url = f"{origin.rstrip('/')}/api/file/getResource?" + urllib.parse.urlencode(
            {"sourceName": source_name}
        )
        content_type = str((records.get(source_name) or {}).get("contentType") or "image/unknown")
        if data is None:
            data, content_type, source_url = download_resource(origin, source_name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            downloaded += 1
            time.sleep(delay)

        reference = references[source_name]
        records[source_name] = {
            "sourceName": source_name,
            "sourceUrl": source_url,
            "localPath": target.relative_to(ASSET_ROOT).as_posix(),
            "contentType": content_type,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "productIds": sorted(reference["productIds"]),
            "roles": sorted(reference["roles"]),
        }
        manifest.update(
            {
                "schemaVersion": 1,
                "catalogVersion": version,
                "sourceOrigin": origin,
                "sourceAccess": "Mirrored with deployer-confirmed authorization.",
                "expectedAssetCount": total,
                "complete": False,
                "assets": [records[key] for key in sorted(records)],
            }
        )
        save_asset_manifest(manifest)
        print(f"asset {index:03d}/{total:03d}: {source_name}")

    manifest["assets"] = [records[key] for key in sorted(references)]
    manifest["expectedAssetCount"] = total
    manifest["complete"] = True
    save_asset_manifest(manifest)
    print(f"verified {total} assets ({downloaded} downloaded) in {ASSET_FILE_ROOT}")
    return manifest


def mysql_value(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, (float, Decimal)):
        return format(Decimal(str(value)), "f")
    text = str(value).replace("\\", "\\\\").replace("'", "''")
    return "'" + text + "'"


def insert_blocks(
    table: str,
    columns: tuple[str, ...],
    rows: list[tuple[Any, ...]],
    chunk_size: int = 200,
    insert_verb: str = "INSERT INTO",
) -> list[str]:
    output: list[str] = []
    for start in range(0, len(rows), chunk_size):
        chunk = rows[start:start + chunk_size]
        output.append(f"{insert_verb} {table} ({', '.join(columns)}) VALUES")
        output.append(",\n".join(
            "    (" + ", ".join(mysql_value(value) for value in row) + ")"
            for row in chunk
        ) + ";")
        output.append("")
    return output


def flatten_category_rows(categories: list[dict[str, Any]]) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    category_rows: list[tuple[Any, ...]] = []
    property_by_id: dict[str, tuple[Any, ...]] = {}
    for parent in categories:
        parent_id = str(parent.get("categoryId") or "")
        category_rows.append((parent_id, parent.get("categoryName"), parent.get("pCategoryId"), parent.get("sort")))
        for child in parent.get("children") or []:
            child_id = str(child.get("categoryId") or "")
            category_rows.append((child_id, child.get("categoryName"), child.get("pCategoryId"), child.get("sort")))
            for prop in child.get("productPropertyList") or []:
                property_id = str(prop.get("propertyId") or "")
                property_by_id[property_id] = (
                    property_id,
                    prop.get("propertyName"),
                    prop.get("pCategoryId"),
                    prop.get("categoryId"),
                    prop.get("propertySort"),
                    prop.get("coverType"),
                )
    return category_rows, [property_by_id[key] for key in sorted(property_by_id)]


def emit_sql(catalog: dict[str, Any]) -> str:
    version = str(catalog["catalogVersion"])
    categories = catalog.get("categories") or []
    details = catalog.get("products") or []
    category_rows, property_definition_rows = flatten_category_rows(categories)
    product_rows: list[tuple[Any, ...]] = []
    property_value_rows: list[tuple[Any, ...]] = []
    sku_rows: list[tuple[Any, ...]] = []
    stock_rows: list[tuple[Any, ...]] = []

    for detail in details:
        product = detail.get("productInfo") or {}
        product_id = str(product["productId"])
        product_rows.append((
            product_id,
            product.get("productName"),
            product.get("productDesc"),
            product.get("cover"),
            product.get("createTime"),
            product.get("categoryId"),
            product.get("pCategoryId"),
            product.get("status"),
            product.get("minPrice"),
            product.get("maxPrice"),
            product.get("totalSale"),
            product.get("commendType"),
        ))
        for prop in detail.get("productPropertyList") or []:
            for sort, value in enumerate(prop.get("propertyValues") or []):
                property_value_rows.append((
                    product_id,
                    prop.get("propertyId"),
                    prop.get("propertyName"),
                    prop.get("propertySort"),
                    prop.get("coverType"),
                    value.get("propertyValueId"),
                    value.get("propertyCover"),
                    value.get("propertyValue"),
                    value.get("propertyRemark"),
                    sort,
                ))
        for sku in detail.get("skuList") or []:
            sku_rows.append((
                product_id,
                sku.get("propertyValueIdHash"),
                sku.get("propertyValueIds"),
                sku.get("price"),
                sku.get("sort"),
            ))
            stock_rows.append((
                product_id,
                sku.get("propertyValueIdHash"),
                sku.get("stock"),
            ))

    incoming_ids = [(row[0],) for row in product_rows]
    legacy_property_ids = ", ".join(mysql_value(item) for item in LEGACY_SYNTHETIC_PROPERTY_IDS)
    legacy_child_category_ids = ", ".join(
        mysql_value(item) for item in LEGACY_SYNTHETIC_CHILD_CATEGORY_IDS
    )
    legacy_parent_category_ids = ", ".join(
        mysql_value(item) for item in LEGACY_SYNTHETIC_PARENT_CATEGORY_IDS
    )
    lines = [
        "-- Generated by data/tools/sync_simlect_catalog.py. Do not edit by hand.",
        f"-- catalog-version: {version}",
        f"-- source-origin: {catalog['sourceOrigin']}",
        "-- Image files are mirrored under data/simlect-assets/file/.",
        "SET NAMES utf8mb4;",
        "SET FOREIGN_KEY_CHECKS = 0;",
        "",
        "CREATE TABLE IF NOT EXISTS simlect_catalog_meta (",
        "    catalog_key varchar(32) NOT NULL PRIMARY KEY,",
        "    catalog_version varchar(64) NOT NULL,",
        "    source_origin varchar(255) NOT NULL,",
        "    product_count int NOT NULL,",
        "    installed_at datetime NOT NULL",
        ") COMMENT 'Authorized Simlect catalog mirror marker';",
        "",
        "CREATE TABLE IF NOT EXISTS simlect_catalog_product (",
        "    product_id varchar(15) NOT NULL PRIMARY KEY,",
        "    catalog_version varchar(64) NOT NULL",
        ") COMMENT 'Products managed by the authorized Simlect mirror';",
        "",
        "START TRANSACTION;",
        "CREATE TEMPORARY TABLE simlect_products_to_replace (",
        "    product_id varchar(15) NOT NULL PRIMARY KEY",
        ");",
        "",
    ]
    lines += insert_blocks("simlect_products_to_replace", ("product_id",), incoming_ids)
    lines += [
        "INSERT IGNORE INTO simlect_products_to_replace (product_id)",
        "SELECT product_id FROM product_info",
        "WHERE product_id REGEXP '^D[0-9]{14}$'",
        "   OR (product_id REGEXP '^P[0-9]{14}$'",
        "       AND (cover = 'https://example.com/cover.png'",
        "            OR product_name REGEXP '^商品-[0-9]+-[0-9]+$'));",
        "",
        "INSERT IGNORE INTO simlect_products_to_replace (product_id)",
        "SELECT product_id FROM simlect_catalog_product;",
        "",
        "DELETE stock FROM aishop_stock.sku_stock stock",
        "INNER JOIN simlect_products_to_replace old ON old.product_id = stock.product_id;",
        "DELETE sku FROM product_sku sku",
        "INNER JOIN simlect_products_to_replace old ON old.product_id = sku.product_id;",
        "DELETE value_row FROM product_property_value value_row",
        "INNER JOIN simlect_products_to_replace old ON old.product_id = value_row.product_id;",
        "DELETE product FROM product_info product",
        "INNER JOIN simlect_products_to_replace old ON old.product_id = product.product_id;",
        "DELETE marker FROM simlect_catalog_product marker",
        "INNER JOIN simlect_products_to_replace old ON old.product_id = marker.product_id;",
        "DROP TEMPORARY TABLE simlect_products_to_replace;",
        "",
        "-- Remove obsolete synthetic definitions only when no surviving data uses them.",
        "DELETE property_def FROM sys_product_property property_def",
        "LEFT JOIN product_property_value value_row",
        "       ON value_row.property_id = property_def.property_id",
        f"WHERE property_def.property_id IN ({legacy_property_ids})",
        "  AND value_row.property_id IS NULL;",
        "",
        "DELETE category FROM sys_category category",
        "LEFT JOIN product_info product",
        "       ON product.category_id = category.category_id",
        "       OR product.p_category_id = category.category_id",
        "LEFT JOIN sys_product_property property_def",
        "       ON property_def.category_id = category.category_id",
        "       OR property_def.p_category_id = category.category_id",
        "LEFT JOIN sys_category child ON child.p_category_id = category.category_id",
        f"WHERE category.category_id IN ({legacy_child_category_ids})",
        "  AND product.product_id IS NULL",
        "  AND property_def.property_id IS NULL",
        "  AND child.category_id IS NULL;",
        "",
        "DELETE category FROM sys_category category",
        "LEFT JOIN product_info product",
        "       ON product.category_id = category.category_id",
        "       OR product.p_category_id = category.category_id",
        "LEFT JOIN sys_product_property property_def",
        "       ON property_def.category_id = category.category_id",
        "       OR property_def.p_category_id = category.category_id",
        "LEFT JOIN sys_category child ON child.p_category_id = category.category_id",
        f"WHERE category.category_id IN ({legacy_parent_category_ids})",
        "  AND product.product_id IS NULL",
        "  AND property_def.property_id IS NULL",
        "  AND child.category_id IS NULL;",
        "",
    ]
    lines += insert_blocks(
        "sys_category",
        ("category_id", "category_name", "p_category_id", "sort"),
        category_rows,
        insert_verb="INSERT IGNORE INTO",
    )
    lines += insert_blocks(
        "sys_product_property",
        ("property_id", "property_name", "p_category_id", "category_id", "property_sort", "cover_type"),
        property_definition_rows,
        insert_verb="INSERT IGNORE INTO",
    )
    lines += insert_blocks(
        "product_info",
        (
            "product_id", "product_name", "product_desc", "cover", "create_time", "category_id",
            "p_category_id", "status", "min_price", "max_price", "total_sale", "commend_type",
        ),
        product_rows,
    )
    lines += insert_blocks(
        "product_property_value",
        (
            "product_id", "property_id", "property_name", "property_sort", "cover_type",
            "property_value_id", "property_cover", "property_value", "property_remark", "sort",
        ),
        property_value_rows,
    )
    lines += insert_blocks(
        "product_sku",
        ("product_id", "property_value_id_hash", "property_value_ids", "price", "sort"),
        sku_rows,
    )
    lines += insert_blocks(
        "aishop_stock.sku_stock",
        ("product_id", "property_value_id_hash", "stock"),
        stock_rows,
    )
    lines += insert_blocks(
        "simlect_catalog_product",
        ("product_id", "catalog_version"),
        [(row[0], version) for row in product_rows],
    )
    lines += [
        "INSERT INTO simlect_catalog_meta",
        "    (catalog_key, catalog_version, source_origin, product_count, installed_at)",
        f"VALUES ('default', {mysql_value(version)}, {mysql_value(catalog['sourceOrigin'])}, {len(product_rows)}, NOW())",
        "ON DUPLICATE KEY UPDATE catalog_version = VALUES(catalog_version),",
        "                        source_origin = VALUES(source_origin),",
        "                        product_count = VALUES(product_count),",
        "                        installed_at = VALUES(installed_at);",
        "COMMIT;",
        "SET FOREIGN_KEY_CHECKS = 1;",
        "",
        "SELECT catalog_version, product_count, installed_at",
        "FROM simlect_catalog_meta WHERE catalog_key = 'default';",
        "SELECT COUNT(*) AS mirrored_product_count FROM simlect_catalog_product;",
        "",
    ]
    return "\n".join(lines)


def build_sql(catalog: dict[str, Any]) -> None:
    SQL_OUT.write_text(emit_sql(catalog), encoding="utf-8")
    print(f"wrote SQL seed to {SQL_OUT}")


def install_assets(catalog: dict[str, Any], project_folder: Path) -> None:
    manifest = json.loads(ASSET_MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("catalogVersion") != catalog.get("catalogVersion") or not manifest.get("complete"):
        raise RuntimeError("asset manifest is incomplete or does not match the catalog version")
    installed = 0
    skipped = 0
    for record in manifest.get("assets") or []:
        source = ASSET_ROOT / str(record["localPath"])
        destination = project_folder / str(record["localPath"])
        source_data = read_local_image(source)
        if source_data is None or hashlib.sha256(source_data).hexdigest() != record["sha256"]:
            raise RuntimeError(f"source asset failed validation: {source}")
        destination_data = read_local_image(destination)
        if destination_data is not None and hashlib.sha256(destination_data).hexdigest() == record["sha256"]:
            skipped += 1
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        installed += 1
    marker = project_folder / ".simlect-catalog-version"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(str(catalog["catalogVersion"]) + "\n", encoding="utf-8")
    print(f"installed {installed} assets, kept {skipped} verified assets in {project_folder / 'file'}")


def check_text(value: Any, maximum: int, label: str) -> None:
    if value is not None and len(str(value)) > maximum:
        raise AssertionError(f"{label} exceeds {maximum} characters")


def validate(catalog: dict[str, Any], require_assets: bool, require_sql: bool) -> None:
    categories = catalog.get("categories") or []
    products = catalog.get("products") or []
    expected_version = catalog_version(categories, products)
    assert catalog.get("catalogVersion") == expected_version
    assert VERSION_FILE.read_text(encoding="utf-8").strip() == expected_version
    ids = [str((detail.get("productInfo") or {}).get("productId") or "") for detail in products]
    assert ids and len(ids) == len(set(ids))
    assert all(re.fullmatch(r"\d{15}", product_id) for product_id in ids)

    sku_count = 0
    property_value_count = 0
    for detail in products:
        product = detail.get("productInfo") or {}
        product_id = str(product["productId"])
        check_text(product.get("productName"), 200, f"{product_id}.productName")
        check_text(product.get("cover"), 500, f"{product_id}.cover")
        assert len([part for part in str(product.get("cover") or "").split(",") if part]) == 5
        assert "https://example.com" not in str(product.get("cover") or "")
        assert str(catalog["sourceOrigin"]) not in str(product.get("cover") or "")
        if product.get("productDesc") is not None:
            assert len(str(product["productDesc"]).encode("utf-8")) <= 65_535
        for prop in detail.get("productPropertyList") or []:
            check_text(prop.get("propertyId"), 10, f"{product_id}.propertyId")
            check_text(prop.get("propertyName"), 30, f"{product_id}.propertyName")
            for value in prop.get("propertyValues") or []:
                property_value_count += 1
                check_text(value.get("propertyValueId"), 15, f"{product_id}.propertyValueId")
                check_text(value.get("propertyCover"), 60, f"{product_id}.propertyCover")
                check_text(value.get("propertyValue"), 100, f"{product_id}.propertyValue")
                check_text(value.get("propertyRemark"), 100, f"{product_id}.propertyRemark")
        sku_list = detail.get("skuList") or []
        assert sku_list, f"{product_id} has no SKU"
        for sku in sku_list:
            sku_count += 1
            assert re.fullmatch(r"[0-9a-f]{32}", str(sku.get("propertyValueIdHash") or ""))
            check_text(sku.get("propertyValueIds"), 500, f"{product_id}.propertyValueIds")
            assert sku.get("stock") is not None

    references = collect_asset_references(catalog)
    if require_assets:
        manifest = json.loads(ASSET_MANIFEST.read_text(encoding="utf-8"))
        assert manifest.get("catalogVersion") == expected_version
        assert manifest.get("complete") is True
        records = {record["sourceName"]: record for record in manifest.get("assets") or []}
        assert set(records) == set(references)
        for source_name, record in records.items():
            target = ASSET_FILE_ROOT / source_name
            data = read_local_image(target)
            assert data is not None, f"invalid local image: {target}"
            assert hashlib.sha256(data).hexdigest() == record["sha256"]

    if require_sql:
        sql = SQL_OUT.read_text(encoding="utf-8")
        assert f"-- catalog-version: {expected_version}" in sql
        assert "cover = 'https://example.com/cover.png'" in sql
        assert "START TRANSACTION;" in sql and "COMMIT;" in sql

    print(
        f"validated {len(products)} products, {property_value_count} property values, "
        f"{sku_count} SKUs, and {len(references)} referenced assets"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sync", action="store_true", help="fetch metadata/assets, build SQL, and validate")
    parser.add_argument("--fetch-metadata", action="store_true", help="fetch/resume category and product details")
    parser.add_argument("--fetch-assets", action="store_true", help="download/resume every referenced image")
    parser.add_argument("--build", action="store_true", help="generate the idempotent SQL seed")
    parser.add_argument("--check", action="store_true", help="validate snapshot, images, and SQL")
    parser.add_argument("--install-to", type=Path, help="install verified assets into PROJECT_FOLDER")
    parser.add_argument("--refresh", action="store_true", help="redownload cached metadata and images")
    parser.add_argument("--limit", type=int, help="sync only the first N products for diagnostics")
    parser.add_argument("--delay", type=float, default=0.20, help="delay between upstream requests")
    parser.add_argument("--origin", default=DEFAULT_ORIGIN, help="authorized source origin")
    args = parser.parse_args()
    if args.sync:
        args.fetch_metadata = args.fetch_assets = args.build = args.check = True
    if not (args.fetch_metadata or args.fetch_assets or args.build or args.check or args.install_to):
        parser.error("select --sync or at least one operation")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    if args.delay < 0 or args.delay > 10:
        parser.error("--delay must be between 0 and 10 seconds")
    return args


def main() -> None:
    args = parse_args()
    catalog = None
    if args.fetch_metadata:
        catalog = fetch_metadata(args.origin, args.delay, args.refresh, args.limit)
    if catalog is None:
        catalog = load_catalog()
    if args.fetch_assets:
        fetch_assets(catalog, args.delay, args.refresh)
    if args.build:
        build_sql(catalog)
    if args.install_to:
        install_assets(catalog, args.install_to.resolve())
    if args.check:
        validate(catalog, require_assets=True, require_sql=True)


if __name__ == "__main__":
    main()
