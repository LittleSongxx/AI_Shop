from __future__ import annotations

import asyncio

import structlog

from app.services.java_internal_client import java_internal_client
from app.utils.biz_payload import first_cover

logger = structlog.get_logger()


class SearchRecommendService:

    async def load_recommend_products(self, user_id: str, limit: int = 8) -> list[dict]:
        """Multi-source personalised recommendation pipeline.

        Sources (parallel):
          1. Category recall — inferred from weighted browse+purchase history
          2. Co-purchase recall — items bought together with the user's recent purchases

        Both lists are merged (category primary, co-purchase fills gaps), then
        MMR-reranked for diversity before being trimmed to `limit`.  Falls back
        to hot-sale when both sources come up empty.
        """
        size = max(1, min(limit, 20))

        # Parallel: resolve preferred category AND purchase history (needed by
        # both the weighted-vote category resolver and the co-purchase path).
        category_result, purchase_result = await asyncio.gather(
            self._resolve_category_from_browse(user_id),
            java_internal_client.purchase_history_product_ids(user_id, limit=3),
            return_exceptions=True,
        )
        category_id: str | None = (
            category_result if not isinstance(category_result, Exception) else None
        )
        purchase_ids: list[str] = (
            purchase_result if not isinstance(purchase_result, Exception) else []
        )

        # Parallel: fetch candidate pools from both sources.
        cat_result, co_result = await asyncio.gather(
            self._load_by_category(category_id, size * 2),
            self._co_purchase_recall(purchase_ids, size),
            return_exceptions=True,
        )
        cat_products: list[dict] = (
            cat_result if not isinstance(cat_result, Exception) else []
        )
        co_products: list[dict] = (
            co_result if not isinstance(co_result, Exception) else []
        )

        merged = self._merge_deduplicate(cat_products, co_products)
        if not merged:
            return await self._load_hot_sale(size)

        return self._mmr_rerank(merged, limit=size)

    # ------------------------------------------------------------------ #
    # Recall sources                                                       #
    # ------------------------------------------------------------------ #

    async def _resolve_category_from_browse(self, user_id: str) -> str | None:
        """Infer a recommendation category from browse and purchase history.

        Strategy:
          - Fetch up to 5 browse IDs (weight 1 each) and up to 3 purchase IDs
            (weight 2 each) concurrently.  Purchase history outweighs browsing
            because buying reveals a stronger preference than clicking.
          - Resolve category_ids for all candidate products in parallel.
          - Return the category with the highest weighted score.

        Falls back gracefully at every step:
          - browse_history_ids() empty → falls back to latest_browse_product_id()
          - individual get_product_detail() failures are skipped
          - no categories resolved → returns None → hot-sale fallback
        """
        if not user_id:
            return None

        # Fetch browse and purchase IDs concurrently.
        browse_result, purchase_result = await asyncio.gather(
            java_internal_client.browse_history_ids(user_id, limit=5),
            java_internal_client.purchase_history_product_ids(user_id, limit=3),
            return_exceptions=True,
        )
        browse_ids: list[str] = (
            browse_result if not isinstance(browse_result, Exception) else []
        )
        purchase_ids: list[str] = (
            purchase_result if not isinstance(purchase_result, Exception) else []
        )

        if not browse_ids:
            single_id = await java_internal_client.latest_browse_product_id(user_id)
            browse_ids = [single_id] if single_id else []

        # Build weight_map — purchase outweighs browse 2:1.
        # If a product appears in both lists, keep the higher weight.
        weight_map: dict[str, int] = {}
        for pid in purchase_ids:
            if pid:
                weight_map[pid] = max(weight_map.get(pid, 0), 2)
        for pid in browse_ids:
            if pid:
                weight_map[pid] = max(weight_map.get(pid, 0), 1)

        if not weight_map:
            return None

        # Fetch product details concurrently; tolerate individual failures.
        pids = list(weight_map)
        detail_results = await asyncio.gather(
            *[java_internal_client.get_product_detail(pid) for pid in pids],
            return_exceptions=True,
        )
        category_scores: dict[str, int] = {}
        for pid, detail in zip(pids, detail_results):
            if isinstance(detail, Exception) or not detail:
                continue
            cat = str(detail.get("category_id") or "").strip()
            if cat:
                category_scores[cat] = category_scores.get(cat, 0) + weight_map[pid]

        if not category_scores:
            return None
        # Highest weighted score wins; ties broken by insertion order
        # (purchase IDs inserted first, so a purchased category wins ties).
        return max(category_scores, key=lambda c: category_scores[c])

    async def _co_purchase_recall(
        self,
        purchase_ids: list[str],
        limit: int,
    ) -> list[dict]:
        """Fetch co-purchased product candidates from the user's purchase history.

        For each recently purchased product, queries which other products were
        frequently bought together in other users' orders — a lightweight item-to-
        item collaborative signal.  Results are merged by combined frequency rank,
        deduplicating against already-purchased products.
        """
        if not purchase_ids:
            return []

        # Fetch co-purchase ID lists for up to 3 seed products in parallel.
        co_id_results = await asyncio.gather(
            *[
                java_internal_client.co_purchase_product_ids(pid, limit=limit)
                for pid in purchase_ids[:3]
            ],
            return_exceptions=True,
        )

        # Merge IDs by combined frequency rank.
        # A product that co-occurs with multiple seeds, or ranks high within one
        # seed's list, gets a higher aggregate score.
        seen_purchased = set(purchase_ids)
        freq: dict[str, float] = {}
        for result in co_id_results:
            if isinstance(result, Exception) or not result:
                continue
            n = len(result)
            for rank, pid in enumerate(result):
                if pid and pid not in seen_purchased:
                    # Higher-ranked positions (lower rank index) get more score.
                    freq[pid] = freq.get(pid, 0.0) + (n - rank)

        if not freq:
            return []

        # Take top candidates sorted by aggregate score.
        top_ids = sorted(freq, key=lambda p: -freq[p])[: limit * 2]

        # Fetch product details concurrently; tolerate individual failures.
        detail_results = await asyncio.gather(
            *[java_internal_client.get_product_detail(pid) for pid in top_ids],
            return_exceptions=True,
        )
        products: list[dict] = []
        for detail in detail_results:
            if isinstance(detail, Exception) or not detail:
                continue
            item = dict(detail)
            item["cover"] = first_cover(item.get("cover"))
            item["_source"] = "co_purchase"
            products.append(item)

        return products[:limit]

    # ------------------------------------------------------------------ #
    # Merge and diversity                                                  #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _merge_deduplicate(
        primary: list[dict],
        secondary: list[dict],
    ) -> list[dict]:
        """Interleave two product lists, deduplicating by product_id.

        Primary products come first; secondary fills gaps with items that were
        not already present, bringing in co-purchase diversity.
        """
        seen: set[str] = set()
        result: list[dict] = []
        for p in primary:
            pid = str(p.get("product_id") or p.get("productId") or "")
            if pid and pid not in seen:
                seen.add(pid)
                result.append(p)
        for p in secondary:
            pid = str(p.get("product_id") or p.get("productId") or "")
            if pid and pid not in seen:
                seen.add(pid)
                result.append(p)
        return result

    @staticmethod
    def _mmr_rerank(
        products: list[dict],
        limit: int,
        lambda_: float = 0.7,
    ) -> list[dict]:
        """Maximal Marginal Relevance reranking for category diversity.

        Balances relevance (original rank position) against redundancy (same
        category_id as an already-selected product).

          score(i) = λ · relevance(i) − (1−λ) · max_sim(i, selected)

        Relevance is rank-reciprocal: 1/(1+rank).
        Similarity is binary on category_id: 1.0 if same, 0.0 otherwise.
        λ=0.7 keeps recommendations relevant while avoiding a page full of
        identical-category products.
        """
        if len(products) <= limit:
            return products

        relevance = [1.0 / (1.0 + i) for i in range(len(products))]
        selected: list[dict] = []
        selected_cats: list[str] = []
        remaining = list(range(len(products)))

        for _ in range(limit):
            if not remaining:
                break
            best_idx: int | None = None
            best_score = float("-inf")
            for i in remaining:
                cat = str(products[i].get("category_id") or "").strip()
                max_sim = (
                    max(1.0 if cat and cat == sc else 0.0 for sc in selected_cats)
                    if selected_cats
                    else 0.0
                )
                score = lambda_ * relevance[i] - (1.0 - lambda_) * max_sim
                if score > best_score:
                    best_score = score
                    best_idx = i
            if best_idx is None:
                break
            selected.append(products[best_idx])
            selected_cats.append(
                str(products[best_idx].get("category_id") or "").strip()
            )
            remaining.remove(best_idx)

        return selected

    # ------------------------------------------------------------------ #
    # Category and hot-sale loaders                                       #
    # ------------------------------------------------------------------ #

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

    # ------------------------------------------------------------------ #
    # Public pass-through helpers used by other services                  #
    # ------------------------------------------------------------------ #

    async def load_hot_sale(self, size: int) -> list[dict]:
        return await self._load_hot_sale(size)

    async def load_by_category(self, category_id: str, size: int) -> list[dict]:
        return await self._load_by_category(category_id, size)


search_recommend_service = SearchRecommendService()
