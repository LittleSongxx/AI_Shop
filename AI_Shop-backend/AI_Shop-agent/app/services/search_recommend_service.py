import structlog

from app.services.java_internal_client import java_internal_client
from app.utils.biz_payload import first_cover

logger = structlog.get_logger()


class SearchRecommendService:

    async def load_recommend_products(self, user_id: str, limit: int = 8) -> list[dict]:

        size = max(1, min(limit, 20))
        category_id = await self._resolve_category_from_browse(user_id)
        products = await self._load_by_category(category_id, size)
        if products:
            return products
        return await self._load_hot_sale(size)

    async def _resolve_category_from_browse(self, user_id: str) -> str | None:

        if not user_id:
            return None
        product_id = await java_internal_client.latest_browse_product_id(user_id)
        if not product_id:
            return None
        detail = await java_internal_client.get_product_detail(product_id)
        if not detail:
            return None
        cat = detail.get("category_id")
        return str(cat) if cat else None

    async def _load_by_category(self, category_id: str | None, size: int) -> list[dict]:

        if not category_id:
            return []
        rows = await java_internal_client.search_on_sale(
            keyword="",
            limit=size,
            category_id=category_id,
        )
        return self._normalize_cards(rows)

    async def _load_hot_sale(self, size: int) -> list[dict]:

        try:
            rows = await java_internal_client.search_on_sale(
                keyword="",
                limit=size,
                hot_sale=True,
            )
            cards = self._normalize_cards(rows)
            if cards:
                return cards
        except Exception as e:
            logger.warning("hot_sale_load_failed_fallback_recent", error=str(e))
        rows = await java_internal_client.search_on_sale(
            keyword="",
            limit=size,
            hot_sale=False,
        )
        return self._normalize_cards(rows)

    @staticmethod
    def _normalize_cards(rows: list[dict]) -> list[dict]:
        out = []
        for r in rows or []:
            item = dict(r)
            item["cover"] = first_cover(item.get("cover"))
            out.append(item)
        return out

    async def load_hot_sale(self, size: int) -> list[dict]:
        return await self._load_hot_sale(size)

    async def load_by_category(self, category_id: str, size: int) -> list[dict]:
        return await self._load_by_category(category_id, size)

search_recommend_service = SearchRecommendService()
