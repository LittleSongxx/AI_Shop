from __future__ import annotations

import asyncio
import hashlib
import time
from datetime import UTC, datetime

import structlog

from app.config.settings import get_settings
from app.services.java_internal_client import java_internal_client
from app.visual.image_processing import NormalizedImage, normalize_query_image
from app.visual.index import VisualIndexError, visual_product_index
from app.visual.provider import visual_provider

logger = structlog.get_logger()


class VisualCatalogIndexer:
    async def index_product(
        self,
        product_id: str,
        *,
        product_version: int | None = None,
        index_name: str | None = None,
    ) -> int:
        version = int(product_version or time.time_ns() // 1_000_000)
        product = await java_internal_client.get_product_detail(product_id)
        if not product or int(product.get("status") or 0) != 1:
            await visual_product_index.delete_product(
                product_id,
                product_version=version,
                index_name=index_name,
            )
            return 0

        covers = _covers(product.get("cover"))
        if not covers:
            await visual_product_index.delete_product(
                product_id,
                product_version=version,
                index_name=index_name,
            )
            return 0
        normalized_images: list[NormalizedImage] = []
        raw_hashes: list[str] = []
        for cover_index in range(len(covers)):
            content, _headers = await java_internal_client.fetch_product_image(
                product_id, cover_index, timeout=20
            )
            raw_hashes.append(hashlib.sha256(content).hexdigest())
            normalized_images.append(normalize_query_image(content))

        settings = get_settings()
        image_embeddings = await asyncio.gather(
            *(visual_provider.embed_image(image.data_uri) for image in normalized_images)
        )
        product_text = _product_text(product)
        fused = await visual_provider.embed_product(
            product_text, [image.data_uri for image in normalized_images]
        )
        common = {
            "productName": str(product.get("product_name") or ""),
            "productText": product_text,
            "categoryId": str(product.get("category_id") or ""),
            "brand": _brand(product),
            "status": 1,
            "minPrice": _number(product.get("min_price")),
            "maxPrice": _number(product.get("max_price")),
            "modelVersion": settings.visual_index_model_version,
            "indexedAt": datetime.now(UTC).isoformat(),
        }
        documents: list[dict] = []
        for cover_index, (image, embedded) in enumerate(
            zip(normalized_images, image_embeddings, strict=True)
        ):
            documents.append(
                {
                    **common,
                    "documentType": "IMAGE",
                    "coverIndex": cover_index,
                    "imageSha256": raw_hashes[cover_index],
                    "normalizedSha256": image.sha256,
                    "embedding": embedded.vector,
                }
            )
        documents.append(
            {
                **common,
                "documentType": "PRODUCT_FUSED",
                "embedding": fused.vector,
            }
        )
        await visual_product_index.replace_product(
            product_id,
            version,
            documents,
            index_name=index_name,
        )
        logger.info(
            "visual_index_product_updated",
            product_id=product_id,
            product_version=version,
            documents=len(documents),
            model_version=settings.visual_index_model_version,
        )
        return len(documents)

    async def rebuild(self, *, concurrency: int = 3) -> dict:
        target = await visual_product_index.ensure_target_index()
        product_ids = await java_internal_client.list_on_sale_product_ids()
        semaphore = asyncio.Semaphore(max(1, min(int(concurrency), 5)))
        failures: dict[str, str] = {}
        indexed = 0

        async def run(product_id: str) -> int:
            async with semaphore:
                try:
                    return await self.index_product(
                        product_id,
                        product_version=int(time.time_ns() // 1_000_000),
                        index_name=target,
                    )
                except Exception as exc:
                    failures[product_id] = type(exc).__name__
                    logger.warning(
                        "visual_index_backfill_product_failed",
                        product_id=product_id,
                        error=type(exc).__name__,
                    )
                    return 0

        if product_ids:
            indexed = sum(await asyncio.gather(*(run(product_id) for product_id in product_ids)))
        if failures:
            raise VisualIndexError(
                f"VISUAL_INDEX_BACKFILL_INCOMPLETE:{len(failures)}/{len(product_ids)}"
            )
        count = await visual_product_index.document_count(target)
        if product_ids and (count < len(product_ids) * 2 or indexed < len(product_ids) * 2):
            raise VisualIndexError("VISUAL_INDEX_BACKFILL_COUNT_MISMATCH")
        await visual_product_index.activate(target, require_documents=bool(product_ids))
        return {
            "index": target,
            "products": len(product_ids),
            "documents": count,
            "modelVersion": get_settings().visual_index_model_version,
        }


def _covers(value: object) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()][:5]


def _product_text(product: dict) -> str:
    parts = [
        str(product.get("product_name") or "").strip(),
        str(product.get("category_name") or product.get("category_id") or "").strip(),
        str(product.get("product_desc") or product.get("description") or "").strip()[:600],
    ]
    for item in product.get("property_values") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("property_name") or "").strip()
        value = str(item.get("property_value") or "").strip()
        if name and value:
            parts.append(f"{name}:{value}")
    return " | ".join(part for part in parts if part)[:2000]


def _brand(product: dict) -> str:
    value = str(product.get("brand") or "").strip()
    if value:
        return value[:100]
    for item in product.get("property_values") or []:
        if isinstance(item, dict) and "品牌" in str(item.get("property_name") or ""):
            return str(item.get("property_value") or "").strip()[:100]
    return ""


def _number(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


visual_catalog_indexer = VisualCatalogIndexer()
