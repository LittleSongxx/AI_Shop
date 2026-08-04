import json
import re
import uuid

import structlog

from app.constants import (
    PRODUCT_CANDIDATE_SIZE,
    PRODUCT_RESULT_SIZE,
    PRODUCT_STATUS_ON_SALE,
    SIMILAR_PRODUCT_SIZE,
)
from app.domain.intent.rules import looks_like_browse_recommend
from app.rag.retriever import rag_retriever
from app.rag.rrf import rrf_merge
from app.services.java_internal_client import java_internal_client
from app.services.product_search_query import (
    filter_products_by_query_relevance,
    normalize_product_search_query,
)
from app.services.redis_service import redis_service
from app.services.search_recommend_service import search_recommend_service
from app.services.shopping_profile_service import shopping_profile_service
from app.utils.biz_payload import build_product_payload, first_cover

logger = structlog.get_logger()

_VAGUE_MARKERS = ("什么", "类似", "同款", "推荐", "有没有", "哪些", "哪个", "吗", "呢", "怎么")

_NOISE_RE = re.compile(r"[\d]+g|[\*×x]\d|装|规格|分享装", re.I)

def is_vague_search_keyword(text: str | None) -> bool:

    t = (text or "").strip()
    if not t or len(t) < 2:
        return True
    if any(m in t for m in _VAGUE_MARKERS) and len(t) <= 16:
        return True
    return False

_SIMILAR_HINTS = ("类似", "同款", "相近", "同类型", "同款式", "别的款", "其他款")


def filter_known_available_products(products: list[dict]) -> list[dict]:
    return [
        product
        for product in products
        if not shopping_profile_service.is_known_out_of_stock(product)
    ]


def is_similar_or_recommend_request(text: str | None) -> bool:

    t = (text or "").strip()
    if not t or len(t) > 40:
        return False
    if any(h in t for h in _SIMILAR_HINTS):
        return True
    if "推荐" in t and len(t) <= 28:
        return True
    return False

def derive_search_keyword(
    keyword: str | None,
    consult_product: dict | None,
) -> str:

    kw = (keyword or "").strip()
    if consult_product and is_vague_search_keyword(kw):

        name = (consult_product.get("productName") or consult_product.get("product_name") or "").strip()
        if name:
            cleaned = _NOISE_RE.sub(" ", name)
            parts = [p.strip() for p in re.split(r"[\s/|]+", cleaned) if p.strip()]
            if parts:
                return " ".join(parts[:4])

        category_id = consult_product.get("categoryId") or consult_product.get("category_id")
        if category_id:
            return f"category:{category_id}"
    # 「我要吃零食」→「零食」，提升关键词/向量命中率
    return normalize_product_search_query(kw) or kw

class ProductService:

    async def search_products(
        self,
        user_id: str,
        keyword: str | None,
        user_text: str = "",
        consult_product: dict | None = None,
        exclude_product_id: str | None = None,
    ) -> tuple[str, str | None, str, list[dict], str]:

        profile = await shopping_profile_service.get_profile(user_id)
        if shopping_profile_service.should_clarify(
            user_text,
            keyword,
            profile,
            consult_product,
        ):
            assistant, biz_data = build_product_payload([])
            return assistant, biz_data, "product_search", [], "clarify"

        query = derive_search_keyword(keyword, consult_product)
        if not query:
            query = (keyword or "").strip()
        if is_vague_search_keyword(query):
            query = (
                derive_search_keyword(None, consult_product)
                or str(profile.get("category") or "").strip()
                or query
            )

        biz_type = "product_search"
        product_ids: list[str] = []
        source = "none"

        if query.startswith("category:"):

            category_id = query.split(":", 1)[1]
            products = await search_recommend_service.load_by_category(category_id, 12)
            source = "category"
        elif query:

            keyword_ids = await rag_retriever.search_product_keyword_ids(query, PRODUCT_CANDIDATE_SIZE)
            vector_ids = await rag_retriever.search_product_vector_ids(query, PRODUCT_CANDIDATE_SIZE)
            # Over-fetch to CANDIDATE_SIZE so term-filtering + reranking have
            # enough headroom; we slice to RESULT_SIZE after reranking.
            product_ids = rrf_merge(keyword_ids, vector_ids, PRODUCT_CANDIDATE_SIZE)
            logger.info(
                "hybrid_search",
                query=query,
                keyword_hits=len(keyword_ids),
                vector_hits=len(vector_ids),
                merged=len(product_ids),
            )
            products = await self._load_products_by_ids(product_ids)
            if products:
                source = "hybrid"
                # Vector/keyword often returns unrelated hot junk — drop non-matching titles.
                relevant = filter_products_by_query_relevance(products, query)
                if not relevant:
                    logger.info(
                        "hybrid_relevance_miss",
                        query=query,
                        candidates=len(products),
                    )
                    products = []
                    source = "none"
                elif len(relevant) < len(products):
                    logger.info(
                        "hybrid_relevance_filtered",
                        query=query,
                        before=len(products),
                        after=len(relevant),
                    )
                    products = relevant
                # Cross-encoder rerank before slicing to RESULT_SIZE.  The
                # circuit breaker inside rerank_products guarantees silent
                # fallback to original RRF order when the API is unavailable.
                if products:
                    products = await rag_retriever.rerank_products(
                        query, products, PRODUCT_RESULT_SIZE
                    )
        else:
            products = []

        if exclude_product_id:
            products = [p for p in products if str(p.get("product_id")) != str(exclude_product_id)]

        similar_intent = _similar_intent(keyword, consult_product)
        if products and similar_intent and source == "hybrid":
            if not _products_match_consult_category(products, consult_product):
                logger.info(
                    "similar_hybrid_category_mismatch",
                    consult_category=_consult_category_id(consult_product),
                )
                products = []
                source = "none"

        if not products and consult_product and is_vague_search_keyword(keyword or user_text):
            # Embedding i2i first: "有没有类似的" deserves content-similar items, not
            # just anything sharing the shelf. Falls back to category internally.
            similar, similar_source = await self.load_similar_products(
                consult_product,
                SIMILAR_PRODUCT_SIZE,
                exclude_product_id or None,
            )
            if similar:
                products = similar
                source = similar_source

        if not products and looks_like_browse_recommend(user_text):
            products = await search_recommend_service.load_recommend_products(user_id, 8)
            if products:
                biz_type = "BROWSE_RECOMMEND"
                source = "browse"

        if not products:
            logger.info("hot_sale_fallback", user_id=user_id)
            products = await search_recommend_service.load_hot_sale(8)
            if products:
                biz_type = "product_search"
                source = "hot_sale"

        candidates_before_stock_filter = len(products)
        products = filter_known_available_products(products)
        if candidates_before_stock_filter and not products:
            logger.info(
                "product_stock_miss",
                user_id=user_id,
                candidates=candidates_before_stock_filter,
            )
            source = "out_of_stock"

        if shopping_profile_service.has_hard_constraints(profile):
            filtered = shopping_profile_service.filter_products(products, profile)
            if products and not filtered:
                logger.info(
                    "shopping_profile_constraint_miss",
                    user_id=user_id,
                    profile=shopping_profile_service.summary(profile),
                    candidates=len(products),
                )
                source = "constraint_miss"
            products = filtered
            if not products and source != "out_of_stock":
                source = "constraint_miss"
        for product in products:
            product["_recommend_reason"] = shopping_profile_service.recommend_reason(
                product,
                profile,
                source,
            )

        # P0-7：一次 serving 一个归因 requestId，同时进卡片 JSON 和曝光日志，
        # 前端点击原样回传，离线分析串起 曝光→点击→(后续加购/成交) 全链。
        request_id = uuid.uuid4().hex
        assistant, biz_data = build_product_payload(products, request_id=request_id)
        try:
            displayed_ids = json.loads(biz_data or "[]")
        except (TypeError, ValueError):
            displayed_ids = []
        if not isinstance(displayed_ids, list):
            displayed_ids = []
        await redis_service.log_impression(
            user_id,
            [str(product_id) for product_id in displayed_ids if product_id],
            query=query,
            source=source,
            request_id=request_id,
        )
        return assistant, biz_data, biz_type, products, source

    async def get_product_detail_text(self, product_id: str) -> str:

        row = await java_internal_client.get_product_detail(product_id)
        if not row:
            return f"【商品不存在】productId={product_id}"
        if row.get("status") != PRODUCT_STATUS_ON_SALE:
            return f"【商品已下架】{row.get('product_name') or product_id}"
        desc = (row.get("product_desc") or "")[:200]
        return (
            f"商品：{row.get('product_name')} | ID：{row.get('product_id')} | "
            f"价格：{row.get('min_price')}~{row.get('max_price')}元 | "
            f"销量：{row.get('total_sale') or 0} | 简介：{desc}"
        )

    async def _load_products_by_ids(self, product_ids: list[str]) -> list[dict]:

        if not product_ids:
            return []
        batch = await java_internal_client.snapshot_batch(product_ids)
        if not batch or not isinstance(batch.get("products"), list):
            logger.warning(
                "product_snapshot_batch_unavailable",
                requested=len(product_ids),
            )
            return []
        rows: list[dict] = batch["products"]

        id_map: dict[str, dict] = {}
        property_values_by_product: dict[str, list[dict]] = {}
        skus_by_product: dict[str, list[dict]] = {}
        total_stocks: dict[str, int] = {}
        if batch:
            if isinstance(batch.get("total_stocks"), dict):
                total_stocks = batch["total_stocks"]
            for prop in batch.get("property_values") or []:
                pid = str(prop.get("product_id") or "")
                if pid:
                    property_values_by_product.setdefault(pid, []).append(prop)
            for sku in batch.get("skus") or []:
                pid = str(sku.get("product_id") or "")
                if pid:
                    skus_by_product.setdefault(pid, []).append(sku)
        for r in rows:
            r = dict(r)
            pid = str(r.get("product_id") or "")
            if not pid:
                continue
            status = r.get("status")
            if status is not None and status != PRODUCT_STATUS_ON_SALE:
                continue
            r["cover"] = first_cover(r.get("cover"))
            properties = property_values_by_product.get(pid)
            if properties:
                r["property_values"] = properties
                brand = next(
                    (
                        prop.get("property_value")
                        for prop in properties
                        if "品牌" in str(prop.get("property_name") or "")
                        and prop.get("property_value")
                    ),
                    None,
                )
                if brand:
                    r["brand"] = brand
            skus = skus_by_product.get(pid)
            if skus:
                r["skus"] = skus
            if pid in total_stocks:
                total_stock = total_stocks[pid]
                r["total_stock"] = total_stock
                try:
                    r["in_stock"] = total_stock is not None and float(total_stock) > 0
                except (TypeError, ValueError):
                    r.pop("total_stock", None)
            id_map[pid] = r

        ordered = []
        for pid in product_ids:
            if pid in id_map:
                ordered.append(id_map[pid])
        return ordered

    async def load_similar_products(
        self,
        anchor: dict | None,
        limit: int = SIMILAR_PRODUCT_SIZE,
        exclude_product_id: str | None = None,
    ) -> tuple[list[dict], str]:
        """Item-to-item recall using the product embeddings already in the index.

        This is the one collaborative-filtering-shaped feature that needs no
        interaction history at all: content similarity comes from the vectors the
        search service already writes. Returns (products, source) so the caller
        can tell embedding recall apart from the category fallback.
        """
        if not anchor:
            return [], "none"
        anchor_id = str(anchor.get("productId") or anchor.get("product_id") or "")
        exclude = str(exclude_product_id or anchor_id or "")
        name = str(anchor.get("productName") or anchor.get("product_name") or "").strip()
        category_id = str(anchor.get("categoryId") or anchor.get("category_id") or "")
        size = max(1, min(int(limit), 20))

        if name:
            # Over-fetch: the anchor itself and off-category hits get dropped below.
            candidate_ids = await rag_retriever.search_product_vector_ids(
                name,
                max(size * 3, PRODUCT_CANDIDATE_SIZE),
            )
            candidate_ids = [pid for pid in candidate_ids if str(pid) != exclude]
            if candidate_ids:
                products = await self._load_products_by_ids(candidate_ids)
                products = [
                    product
                    for product in products
                    if str(product.get("product_id") or "") != exclude
                ]
                if category_id:
                    same_category = [
                        product
                        for product in products
                        if str(product.get("category_id") or "") == category_id
                    ]
                    # Only tighten to the anchor's shelf when it still fills the row;
                    # a near-empty same-category list is worse than a mixed one.
                    if len(same_category) >= min(size, 3):
                        products = same_category
                products = filter_known_available_products(products)[:size]
                if products:
                    logger.info(
                        "similar_i2i_embedding_hit",
                        anchor_id=anchor_id,
                        category_id=category_id,
                        returned=len(products),
                    )
                    return products, "similar_i2i"

        if category_id:
            products = await search_recommend_service.load_by_category(category_id, size)
            products = [
                product
                for product in products
                if str(product.get("product_id") or "") != exclude
            ]
            if products:
                logger.info(
                    "similar_i2i_category_fallback",
                    anchor_id=anchor_id,
                    category_id=category_id,
                    returned=len(products),
                )
                return products[:size], "category"
        return [], "none"

product_service = ProductService()

def _similar_intent(keyword: str | None, consult: dict | None) -> bool:

    return is_vague_search_keyword(keyword) and bool(consult)

def _consult_category_id(consult: dict | None) -> str:

    if not consult:
        return ""
    return str(consult.get("categoryId") or consult.get("category_id") or "")

def _products_match_consult_category(products: list[dict], consult: dict | None) -> bool:

    consult_cat = _consult_category_id(consult)
    if not consult_cat or not products:
        return False
    return any(str(p.get("category_id") or "") == consult_cat for p in products)

def format_search_tool_message(
    keyword: str,
    consult: dict | None,
    products: list[dict],
    source: str,
    profile: dict | None = None,
) -> str:

    from app.domain.intent.rules import looks_like_browse_recommend, looks_like_hot_sale_recommend
    from app.services.product_search_query import filter_products_by_query_relevance

    consult_name = (consult or {}).get("productName") or (consult or {}).get("product_name") or "当前商品"
    similar_intent = _similar_intent(keyword, consult)
    alternative_sources = {"browse", "hot_sale"}
    kw = (keyword or "").strip()
    kw_display = (normalize_product_search_query(kw) or kw)[:24] if kw else "你的需求"

    if source == "clarify":
        return (
            "【需求澄清】为了更准确地推荐，请告诉我商品类别、预算或使用场景，"
            "例如“3000元以内的办公笔记本”。"
        )
    if source == "constraint_miss":
        summary = shopping_profile_service.summary(profile)
        detail = f"（{summary}）" if summary else ""
        return (
            f"【筛选结果】暂未找到同时满足你的条件{detail}的在售商品。\n"
            "可以放宽预算或品牌范围，也可以告诉我可接受的替代条件。"
        )
    if source == "out_of_stock":
        return (
            f"【库存提示】与「{kw_display}」匹配的商品当前均已售罄。\n"
            "你可以换个关键词，或告诉我可接受的替代品类和品牌。"
        )

    if similar_intent and source in alternative_sources:
        return (
            f"【类似商品】暂未找到与「{consult_name}」类似或同款的商品。\n"
            f"【另荐热销】已为您另外推荐热销商品（非同款，请查看下方卡片）。"
        )
    if similar_intent and source == "category":
        return f"【同品类推荐】找到 {len(products)} 个同品类商品（请查看下方卡片）。"
    if source == "similar_i2i":
        return (
            f"【类似商品】根据「{consult_name}」为你找到 {len(products)} 个相似商品"
            "（请查看下方卡片）。"
        )

    # Even if source claims hybrid, never brand irrelevant titles as「找到」.
    # category/similar_i2i are excluded: both are recalled by shelf or embedding
    # rather than by the keyword, so keyword-term matching would wrongly reject them.
    if products and kw and source not in ("category", "similar_i2i"):
        relevant = filter_products_by_query_relevance(products, kw)
        intentional_alt = looks_like_hot_sale_recommend(kw) or looks_like_browse_recommend(kw)
        if not relevant and not intentional_alt:
            return (
                f"【搜索结果】暂未找到与「{kw_display}」相关的商品。\n"
                f"【另荐热销】已为您另外推荐热销商品，请查看下方卡片。"
            )

    # Keyword search missed → hot-sale / browse backfill: never label as「搜索结果找到」.
    if products and source in alternative_sources:
        intentional_alt = looks_like_hot_sale_recommend(kw) or looks_like_browse_recommend(kw)
        if intentional_alt and source == "hot_sale":
            return f"【热销推荐】为您推荐 {len(products)} 个热销商品（请查看下方卡片）。"
        if intentional_alt and source == "browse":
            return f"【浏览推荐】根据你的浏览为你推荐 {len(products)} 个商品（请查看下方卡片）。"
        alt_label = "【另荐热销】" if source == "hot_sale" else "【浏览推荐】"
        alt_body = (
            "已为您另外推荐热销商品，请查看下方卡片。"
            if source == "hot_sale"
            else "已根据浏览为您另外推荐商品，请查看下方卡片。"
        )
        return (
            f"【搜索结果】暂未找到与「{kw_display}」相关的商品。\n"
            f"{alt_label}{alt_body}"
        )

    if not products:
        if kw:
            return f"【搜索结果】暂未找到与「{kw_display}」相关的商品。"
        return "【搜索结果】未找到相关商品。"
    return f"【搜索结果】找到 {len(products)} 个商品（请查看下方卡片）。"
