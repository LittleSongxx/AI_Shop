import copy
import json
import re
import time
from collections.abc import Mapping
from typing import Any

import structlog

from app.config.settings import get_settings
from app.constants import (
    PRODUCT_CANDIDATE_SIZE,
    PRODUCT_RESULT_SIZE,
    PRODUCT_STATUS_ON_SALE,
    SIMILAR_PRODUCT_SIZE,
)
from app.domain.intent.rules import (
    looks_like_browse_recommend,
    looks_like_hot_sale_recommend,
)
from app.rag.retriever import rag_retriever
from app.services.episode_service import episode_service
from app.services.java_internal_client import java_internal_client
from app.services.product_search_pipeline import (
    build_product_query_plan,
    product_search_pipeline,
)
from app.services.product_search_query import (
    normalize_product_search_query,
)
from app.services.recommendation_attribution_service import (
    recommendation_attribution_service,
)
from app.services.search_recommend_service import search_recommend_service
from app.services.shopping_decision_service import shopping_decision_service
from app.services.shopping_mission_service import (
    apply_explicit_turn,
    empty_shopping_mission,
    mission_is_active,
    mission_summary,
    next_clarification,
    shopping_mission_service,
)
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


def derive_raw_search_query(
    keyword: str | None,
    consult_product: dict | None,
) -> str:
    """Preserve the complete current-turn request for hybrid retrieval."""

    value = (keyword or "").strip()
    if consult_product and is_vague_search_keyword(value):
        name = str(consult_product.get("productName") or consult_product.get("product_name") or "").strip()
        if name:
            cleaned = _NOISE_RE.sub(" ", name)
            parts = [part.strip() for part in re.split(r"[\s/|]+", cleaned) if part.strip()]
            if parts:
                return " ".join(parts[:4])
        category_id = consult_product.get("categoryId") or consult_product.get("category_id")
        if category_id:
            return f"category:{category_id}"
    return value


def _constraint_value(constraints: Mapping[str, Any], *keys: str) -> Any:
    return next(
        (
            constraints[key]
            for key in keys
            if key in constraints and constraints[key] is not None
        ),
        None,
    )


def _constraint_terms(constraints: Mapping[str, Any], *keys: str) -> list[str]:
    value = _constraint_value(constraints, *keys)
    if not isinstance(value, (list, tuple, set)):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item or "").strip()
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def merge_runtime_constraints(
    mission: dict,
    constraints: Mapping[str, Any] | None,
) -> dict:
    """Overlay explicit v1 request constraints without mutating durable memory."""
    if not constraints:
        return mission
    resolved = copy.deepcopy(mission)
    hard = dict(resolved.get("hardConstraints") or {})
    soft = dict(resolved.get("softPreferences") or {})
    exclusions = dict(resolved.get("exclusions") or {})

    category = _constraint_value(constraints, "category")
    if str(category or "").strip():
        resolved["category"] = str(category).strip()
    budget_min = _constraint_value(constraints, "budgetMin", "budget_min")
    budget_max = _constraint_value(constraints, "budgetMax", "budget_max")
    if budget_min is not None:
        hard["budgetMin"] = budget_min
    if budget_max is not None:
        hard["budgetMax"] = budget_max
    required_brands = _constraint_terms(
        constraints, "requiredBrands", "required_brands"
    )
    if required_brands:
        hard["requiredBrands"] = required_brands
    excluded_brands = _constraint_terms(
        constraints, "excludedBrands", "excluded_brands"
    )
    if excluded_brands:
        exclusions["brands"] = excluded_brands
    excluded_terms = _constraint_terms(
        constraints, "excludedTerms", "excluded_terms"
    )
    if excluded_terms:
        exclusions["terms"] = excluded_terms
    use_cases = _constraint_terms(constraints, "useCases", "use_cases")
    if use_cases:
        resolved["useCases"] = use_cases
    preferred_features = _constraint_terms(
        constraints, "preferredFeatures", "preferred_features"
    )
    if preferred_features:
        soft["features"] = preferred_features

    resolved["hardConstraints"] = hard
    resolved["softPreferences"] = soft
    resolved["exclusions"] = exclusions
    resolved["runtimeConstraintSource"] = "recommendation/v1"
    return resolved

class ProductService:

    async def decide_verified_candidates(
        self,
        *,
        user_id: str,
        candidates: list[dict],
        user_text: str,
        request_id: str,
        runtime_constraints: Mapping[str, Any] | None = None,
        source: str = "verified_candidates",
    ) -> tuple[list[dict], str]:
        profile = await shopping_profile_service.get_effective_profile(user_id)
        mission = await self._mission_for_request(
            user_id=user_id,
            user_text=user_text,
            profile=profile,
            runtime_constraints=runtime_constraints,
        )
        decision = await shopping_decision_service.decide(
            user_id=user_id,
            mission=mission,
            candidates=candidates,
            source=source,
            request_id=request_id,
            user_text=user_text,
        )
        return decision.products, decision.source

    async def search_products(
        self,
        user_id: str,
        keyword: str | None,
        user_text: str = "",
        consult_product: dict | None = None,
        exclude_product_id: str | None = None,
        request_id: str | None = None,
        runtime_constraints: Mapping[str, Any] | None = None,
    ) -> tuple[str, str | None, str, list[dict], str]:
        search_started = time.perf_counter()
        query = derive_raw_search_query(keyword, consult_product)
        if not query:
            query = (keyword or "").strip()
        profile = await shopping_profile_service.get_effective_profile(user_id)
        mission = await self._mission_for_request(
            user_id=user_id,
            user_text=user_text or keyword or "",
            profile=profile,
            runtime_constraints=runtime_constraints,
        )
        query_plan_started = time.perf_counter()
        query_plan = build_product_query_plan(query, mission)
        query_parse_ms = round((time.perf_counter() - query_plan_started) * 1000, 4)

        # A concrete keyword is already enough to identify a shelf.  Otherwise
        # choose exactly one high-impact decision slot before spending model or
        # retrieval budget.  The choice is bounded to two turns in the mission.
        clarification = next_clarification(mission)
        should_ask_clarification = shopping_profile_service.should_clarify(
            user_text or query,
            query,
            profile,
            consult_product,
        )
        if (
            clarification
            and should_ask_clarification
            and not (
                clarification.get("slot") == "category"
                and self._has_concrete_query(query_plan.raw_query, consult_product)
            )
        ):
            episode_service.record_step(
                "SHOPPING_CLARIFICATION_DECISION",
                node_name="shopping_mission",
                status="OK",
                output_data={
                    "missionId": clarification.get("missionId"),
                    "slot": clarification.get("slot"),
                    "question": clarification.get("question"),
                    "options": clarification.get("options") or [],
                    "reason": clarification.get("reason"),
                    "clarificationCount": int(
                        (mission or {}).get("clarificationCount") or 0
                    )
                    + 1,
                    "maxClarifications": get_settings().shopping_mission_max_clarifications,
                },
                agent_id="shopping_advisor",
            )
            await shopping_mission_service.mark_clarification_presented(user_id, mission)
            assistant = json.dumps(clarification, ensure_ascii=False)
            return assistant, None, "shopping_decision_v2", [], "clarify"

        if is_vague_search_keyword(query):
            query = (
                derive_search_keyword(None, consult_product)
                or str(mission.get("category") or profile.get("category") or "").strip()
                or query
            )
            query_plan = build_product_query_plan(query, mission)

        product_ids: list[str] = []
        source = "none"

        if query.startswith("category:"):

            category_id = query.split(":", 1)[1]
            products = await search_recommend_service.load_by_category(category_id, 12)
            source = "category"
        elif query:
            search_result = await product_search_pipeline.search(
                query_plan,
                candidate_size=PRODUCT_CANDIDATE_SIZE,
                result_size=PRODUCT_RESULT_SIZE,
                keyword_search=rag_retriever.search_product_keyword_ids,
                vector_search=rag_retriever.search_product_vector_ids,
                load_products=self._load_products_by_ids,
                rerank=rag_retriever.rerank_products,
                deadline_seconds=get_settings().product_search_deadline_seconds,
                provider_timeout_seconds=get_settings().product_search_provider_timeout_seconds,
            )
            search_result.trace.stage_latency_ms["queryParse"] = query_parse_ms
            product_ids = search_result.ranked_ids
            products = search_result.products
            logger.info(
                "hybrid_search",
                query=query_plan.raw_query,
                variants=list(query_plan.retrieval_variants),
                merged=len(product_ids),
                results=len(products),
            )
            if products:
                source = "hybrid"
            episode_service.record_step(
                "PRODUCT_SEARCH_PIPELINE",
                node_name="product_search",
                input_data={"queryPlan": query_plan.public()},
                output_data={"trace": search_result.trace.public()},
                latency_ms=round((time.perf_counter() - search_started) * 1000),
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
                source = "browse"

        # Hot-sale is a valid explicit request, never an undisclosed fallback
        # for a failed query or a constrained shopping mission.
        if not products and looks_like_hot_sale_recommend(user_text):
            products = await search_recommend_service.load_hot_sale(8)
            if products:
                source = "hot_sale_explicit"

        candidates_before_stock_filter = len(products)
        products = filter_known_available_products(products)
        if candidates_before_stock_filter and not products:
            logger.info(
                "product_stock_miss",
                user_id=user_id,
                candidates=candidates_before_stock_filter,
            )
            source = "out_of_stock"

        if not products:
            # A search miss is not permission to show unrelated products.  The
            # user sees the conflict and may revise one constraint explicitly.
            return "[]", None, "shopping_decision_v2", [], source or "constraint_miss"

        if not get_settings().shopping_decision_v2_enabled:
            # This is an operational escape hatch only. New default traffic
            # always travels through the v2 decision contract below.
            assistant, biz_data = build_product_payload(products, request_id=request_id)
            return assistant, biz_data, "product_search", products, source

        recall_source = source
        decision_started = time.perf_counter()
        decision = await shopping_decision_service.decide(
            user_id=user_id,
            mission=mission,
            candidates=products,
            source=recall_source,
            request_id=request_id,
            user_text=user_text or keyword or "",
        )
        if source == "hybrid":
            search_result.trace.stage_latency_ms["shoppingDecision"] = round(
                (time.perf_counter() - decision_started) * 1000, 4
            )
            search_result.trace.stage_latency_ms["productServiceTotal"] = round(
                (time.perf_counter() - search_started) * 1000, 4
            )
            search_result.trace.result_source = decision.source
        products = decision.products
        if not products:
            return "[]", None, "shopping_decision_v2", [], decision.source

        for product in products:
            product.setdefault("decision_recall_source", recall_source)
        assistant, biz_data = build_product_payload(products, request_id=decision.request_id)
        await recommendation_attribution_service.record_impression(
            user_id,
            [
                str(product.get("product_id") or product.get("productId") or "")
                for product in products
            ],
            query=query,
            source=recall_source,
            request_id=decision.request_id,
        )
        return assistant, biz_data, "shopping_decision_v2", products, decision.source

    async def _mission_for_request(
        self,
        *,
        user_id: str,
        user_text: str,
        profile: dict,
        runtime_constraints: Mapping[str, Any] | None = None,
    ) -> dict:
        """Resolve the single active shopping state without reviving ShoppingNeed.

        Normal requests have already persisted a mission at API ingress.  The
        small ephemeral fallback keeps direct MCP calls governed as well, while
        intentionally avoiding a second online state store.
        """
        mission = await shopping_mission_service.load(user_id)
        if mission_is_active(mission):
            return merge_runtime_constraints(mission, runtime_constraints)
        derived = apply_explicit_turn(
            None,
            profile=profile,
            user_text=user_text,
            message_id=0,
        )
        resolved = derived if mission_is_active(derived) else empty_shopping_mission(profile)
        return merge_runtime_constraints(resolved, runtime_constraints)

    @staticmethod
    def _has_concrete_query(query: str, consult_product: dict | None) -> bool:
        if consult_product and is_vague_search_keyword(query):
            return True
        normalized = str(query or "").strip()
        return bool(normalized and not is_vague_search_keyword(normalized))

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

    async def load_verified_products(self, product_ids: list[str]) -> list[dict]:
        """Resolve current Java-owned sale/stock snapshots in requested order."""
        return filter_known_available_products(await self._load_products_by_ids(product_ids))

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
    mission: dict | None = None,
) -> str:

    from app.domain.intent.rules import looks_like_browse_recommend, looks_like_hot_sale_recommend
    from app.services.product_search_query import filter_products_by_query_relevance

    consult_name = (consult or {}).get("productName") or (consult or {}).get("product_name") or "当前商品"
    similar_intent = _similar_intent(keyword, consult)
    alternative_sources = {"browse", "hot_sale", "hot_sale_explicit"}
    kw = (keyword or "").strip()
    kw_display = (normalize_product_search_query(kw) or kw)[:24] if kw else "你的需求"
    uncertainty_suffix = (
        " 当前条件仍不完整，结果按较宽范围返回，适配性存在不确定性。"
        if (mission or {}).get("uncertaintyDisclosureRequired")
        else ""
    )

    # Keep hard negative constraints visible in the user-facing explanation.
    # This is a disclosure of the requested filter, not a claim that the
    # entire catalogue contains no excluded items.
    exclusion_terms: list[str] = []
    for raw in (
        *((profile or {}).get("excludedBrands") or []),
        *((profile or {}).get("excludedTerms") or []),
        *(((mission or {}).get("exclusions") or {}).get("brands") or []),
        *(((mission or {}).get("exclusions") or {}).get("terms") or []),
    ):
        value = str(raw or "").strip()
        if value and value.casefold() not in {item.casefold() for item in exclusion_terms}:
            exclusion_terms.append(value)
    exclusion_disclosure = (
        f"排除：{'、'.join(exclusion_terms[:5])}" if exclusion_terms else ""
    )

    if source == "clarify":
        clarification = next_clarification(mission)
        question = (clarification or {}).get("question") or "你最看重哪一项条件？"
        return f"【需求澄清】{question}"
    if source in {"constraint_miss", "no_match", "none"}:
        summary = mission_summary(mission) or shopping_profile_service.summary(profile)
        detail = f"（{summary}）" if summary else ""
        return (
            f"【筛选结果】本次检索暂未返回同时满足你的条件{detail}的商品，"
            "不能据此断言平台无货。\n"
            "可以放宽预算或品牌范围，也可以告诉我可接受的替代条件后继续检索。"
        )
    if source == "offer_unavailable":
        return "【报价核验】当前无法核验实时价格、库存或优惠，暂不展示可能无法购买的推荐。请稍后重试。"
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
    if products and kw and source not in ("category", "similar_i2i", "shopping_decision_v2"):
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
        if intentional_alt and source in {"hot_sale", "hot_sale_explicit"}:
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
            return (
                f"【搜索结果】本次检索暂未返回与「{kw_display}」匹配的商品，"
                "不能据此断言平台无货。"
            )
        return "【搜索结果】本次检索暂未返回匹配商品，不能据此断言平台无货。"
    if source == "shopping_decision_v2":
        summary = mission_summary(mission)
        suffix = f"，已按{summary}筛选" if summary else ""
        if exclusion_disclosure and exclusion_disclosure not in suffix:
            suffix += f"；{exclusion_disclosure}"
        return (
            f"【可信导购】已核验指定 SKU 的实时价格、库存和可用优惠{suffix}，"
            f"请查看下方对比卡片。{uncertainty_suffix}"
        )
    return f"【搜索结果】找到 {len(products)} 个商品（请查看下方卡片）。"
