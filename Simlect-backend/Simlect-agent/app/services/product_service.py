import re
import structlog

from app.constants import PRODUCT_CANDIDATE_SIZE, PRODUCT_RESULT_SIZE, PRODUCT_STATUS_ON_SALE
from app.domain.intent.rules import looks_like_browse_recommend
from app.rag.retriever import rag_retriever
from app.rag.rrf import rrf_merge
from app.services.java_internal_client import java_internal_client
from app.services.search_recommend_service import search_recommend_service
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
    return kw

class ProductService:

    async def search_products(
        self,
        user_id: str,
        keyword: str | None,
        user_text: str = "",
        consult_product: dict | None = None,
        exclude_product_id: str | None = None,
    ) -> tuple[str, str | None, str, list[dict], str]:

        query = derive_search_keyword(keyword, consult_product)
        if not query:
            query = (keyword or "").strip()
        if is_vague_search_keyword(query):
            query = derive_search_keyword(None, consult_product) or query

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
            product_ids = rrf_merge(keyword_ids, vector_ids, PRODUCT_RESULT_SIZE)
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
            category_id = consult_product.get("categoryId") or consult_product.get("category_id")
            if category_id:
                logger.info("category_fallback", category_id=category_id)
                products = await search_recommend_service.load_by_category(str(category_id), 8)
                if exclude_product_id:
                    products = [p for p in products if str(p.get("product_id")) != str(exclude_product_id)]
                if products:
                    source = "category"

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

        assistant, biz_data = build_product_payload(products)
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
        rows: list[dict] = []
        if batch and isinstance(batch.get("products"), list):
            rows = batch["products"]
        else:

            for pid in product_ids:
                detail = await java_internal_client.get_product_detail(pid)
                if detail:
                    rows.append(detail)

        id_map: dict[str, dict] = {}
        for r in rows:
            pid = str(r.get("product_id") or "")
            if not pid:
                continue
            status = r.get("status")
            if status is not None and status != PRODUCT_STATUS_ON_SALE:
                continue
            r["cover"] = first_cover(r.get("cover"))
            id_map[pid] = r

        ordered = []
        for pid in product_ids:
            if pid in id_map:
                ordered.append(id_map[pid])
        return ordered

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
) -> str:

    consult_name = (consult or {}).get("productName") or (consult or {}).get("product_name") or "当前商品"
    similar_intent = _similar_intent(keyword, consult)
    alternative_sources = {"browse", "hot_sale"}

    if similar_intent and source in alternative_sources:
        return (
            f"【类似商品】暂未找到与「{consult_name}」类似或同款的商品。\n"
            f"【另荐热销】已为您另外推荐热销商品（非同款，请查看下方卡片）。"
        )
    if similar_intent and source == "category":
        return f"【同品类推荐】找到 {len(products)} 个同品类商品（请查看下方卡片）。"
    if not products:
        return "【搜索结果】未找到相关商品。"
    return f"【搜索结果】找到 {len(products)} 个商品（请查看下方卡片）。"
