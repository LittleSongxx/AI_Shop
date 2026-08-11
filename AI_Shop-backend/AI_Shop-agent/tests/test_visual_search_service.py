from __future__ import annotations

import io
import json
from unittest.mock import AsyncMock, Mock

import pytest
from PIL import Image

from app.harness.agents.contracts import VerifiedImageContext, VisualSubject
from app.services.shopping_decision_service import ShoppingDecisionResult
from app.services.tool_invoke_result import ToolInvokeResult
from app.visual.contracts import (
    GroundingResult,
    VisualEmbeddingResult,
    VisualIndexHit,
    VisualProviderError,
    VisualProviderMetadata,
)
from app.visual.search_service import VisualProductSearchService, _weighted_rrf


def _jpeg() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (640, 480), color=(230, 230, 230)).save(output, format="JPEG")
    return output.getvalue()


def _image_context(subject: VisualSubject | None = None) -> VerifiedImageContext:
    return VerifiedImageContext(
        asset_id="img_0123456789abcdef0123456789abcdef",
        content_sha256="a" * 64,
        mime_type="image/jpeg",
        width=640,
        height=480,
        scene="agent",
        selected_subject=subject,
    )


def _metadata(capability: str) -> VisualProviderMetadata:
    return VisualProviderMetadata(
        capability=capability,
        model=f"test-{capability}",
        request_id="request-1",
    )


def _hit(
    product_id: str,
    *,
    cosine: float | None = 0.8,
    source: str = "image_knn",
    cover_index: int = 0,
) -> VisualIndexHit:
    return VisualIndexHit(
        product_id=product_id,
        document_id=f"doc-{product_id}",
        document_type="IMAGE",
        cover_index=cover_index,
        image_sha256=None,
        normalized_sha256=None,
        product_name=f"商品 {product_id}",
        category_id="C1",
        brand=None,
        score=0.9,
        cosine=cosine,
        recall_source=source,
    )


def _offered(product: dict, *, request_suffix: str = "1") -> dict:
    price = float(product.get("min_price") or product.get("minPrice") or 100)
    product_id = str(product.get("product_id") or product.get("productId") or "")
    return {
        **product,
        "status": "1",
        "in_stock": True,
        "base_price": price,
        "estimated_payable": price,
        "offer_snapshot_id": f"offer-{product_id}-{request_suffix}",
        "sku_key": f"sku-{product_id}",
        "coupon_status": "UNAVAILABLE",
        "quote_expires_at": "2999-08-10T12:00:00Z",
        "ranking": {"utilityScore": 0.8, "policyVersion": "test"},
        "recommendation": {
            "role": "用途匹配优先",
            "summary": "已通过测试中的权威报价校验",
            "bestFor": "当前用途",
            "tradeoff": "请核对关键规格",
        },
    }


@pytest.fixture
def visual_dependencies(monkeypatch):
    monkeypatch.setattr(
        "app.visual.search_service.java_internal_client.fetch_agent_image",
        AsyncMock(return_value=(_jpeg(), {"content-type": "image/jpeg"})),
    )
    monkeypatch.setattr(
        "app.visual.search_service.shopping_profile_service.get_effective_profile",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        "app.visual.search_service.shopping_mission_service.load",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.visual.search_service.episode_service.record_step", lambda *_args, **_kwargs: None
    )


@pytest.mark.asyncio
async def test_multiple_subjects_stop_before_embedding_and_return_selection_card(
    monkeypatch, visual_dependencies
):
    subjects = [
        VisualSubject(subject_id="subject_1", label="运动鞋", bbox=(20, 30, 450, 800)),
        VisualSubject(subject_id="subject_2", label="背包", bbox=(500, 50, 950, 900)),
    ]
    monkeypatch.setattr(
        "app.visual.search_service.visual_provider.locate_subjects",
        AsyncMock(
            return_value=GroundingResult(
                subjects=subjects,
                metadata=_metadata("grounding"),
            )
        ),
    )
    embed = AsyncMock()
    monkeypatch.setattr("app.visual.search_service.visual_provider.embed_image", embed)
    create = AsyncMock(
        return_value={
            "type": "VISUAL_SUBJECT_SELECTION",
            "selectionId": "visual-selection-1",
            "subjects": [subject.model_dump(mode="json") for subject in subjects],
            "expiresAt": "2026-08-10T10:30:00+08:00",
        }
    )
    monkeypatch.setattr("app.visual.search_service.visual_selection_store.create", create)

    result = await VisualProductSearchService().search(
        user_id="u1",
        image_context=_image_context(),
        query_text="帮我找图里的商品",
        source_message_id=42,
    )

    assert result.biz_type == "visual_subject_selection"
    assert json.loads(result.assistant_cards)["selectionId"] == "visual-selection-1"
    assert result.retrieval_trace["outcome"] == "NEEDS_SUBJECT_SELECTION"
    embed.assert_not_awaited()
    create.assert_awaited_once()


@pytest.mark.asyncio
async def test_image_with_text_constraints_runs_image_fused_and_text_recall(
    monkeypatch, visual_dependencies
):
    subject = VisualSubject(
        subject_id="subject_1", label="运动鞋", bbox=(20, 30, 850, 900)
    )
    monkeypatch.setattr(
        "app.visual.search_service.visual_provider.locate_subjects",
        AsyncMock(
            return_value=GroundingResult(
                subjects=[subject],
                metadata=_metadata("grounding"),
            )
        ),
    )
    embedding = VisualEmbeddingResult(
        vector=[0.1] * 1024,
        metadata=_metadata("embedding"),
    )
    embed = AsyncMock(side_effect=[embedding, embedding])
    monkeypatch.setattr("app.visual.search_service.visual_provider.embed_image", embed)
    monkeypatch.setattr(
        "app.visual.search_service.visual_product_index.exact_hash_hits",
        AsyncMock(return_value=[]),
    )
    image_hits = [_hit("P1")]
    fused_hits = [_hit("P1", source="product_fused_knn")]
    knn = AsyncMock(side_effect=[image_hits, fused_hits])
    text = AsyncMock(return_value=[])
    monkeypatch.setattr("app.visual.search_service.visual_product_index.search_knn", knn)
    monkeypatch.setattr("app.visual.search_service.visual_product_index.search_text", text)
    expected = ToolInvokeResult(content="ok", product_ids=["P1"])
    finalize = AsyncMock(return_value=expected)
    service = VisualProductSearchService()
    monkeypatch.setattr(service, "_finalize_hits", finalize)

    result = await service.search(
        user_id="u1",
        image_context=_image_context(),
        query_text="帮我找类似的，500 元以内的红色运动鞋",
        source_message_id=42,
    )

    assert result is expected
    assert embed.await_count == 2
    assert embed.await_args_list[1].kwargs["text"] == "500 元以内的红色运动鞋"
    assert knn.await_count == 2
    expected_filters = {"budgetMax": 500.0, "category": "鞋子"}
    assert knn.await_args_list[0].kwargs["filters"] == expected_filters
    text.assert_awaited_once_with(
        "500 元以内的红色运动鞋",
        size=20,
        filters=expected_filters,
    )
    finalized_hits = finalize.await_args.kwargs["hits"]
    assert [hit.product_id for hit in finalized_hits] == ["P1"]
    assert finalize.await_args.kwargs["selected_subject"] == subject


@pytest.mark.asyncio
async def test_embedding_failure_uses_explicit_understanding_fallback(
    monkeypatch, visual_dependencies
):
    monkeypatch.setattr(
        "app.visual.search_service.visual_provider.locate_subjects",
        AsyncMock(
            return_value=GroundingResult(subjects=[], metadata=_metadata("grounding"))
        ),
    )
    monkeypatch.setattr(
        "app.visual.search_service.visual_product_index.exact_hash_hits",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "app.visual.search_service.visual_provider.embed_image",
        AsyncMock(
            side_effect=VisualProviderError(
                "VISUAL_EMBEDDING_TEMPORARILY_UNAVAILABLE", retryable=True
            )
        ),
    )
    expected = ToolInvokeResult(content="按图片内容理解推荐")
    service = VisualProductSearchService()
    fallback = AsyncMock(return_value=expected)
    monkeypatch.setattr(service, "_understanding_fallback", fallback)

    result = await service.search(
        user_id="u1",
        image_context=_image_context(),
        query_text="帮我找类似商品",
        source_message_id=42,
    )

    assert result is expected
    fallback.assert_awaited_once()
    assert (
        fallback.await_args.kwargs["trace"]["embeddingError"]
        == "VISUAL_EMBEDDING_TEMPORARILY_UNAVAILABLE"
    )


@pytest.mark.asyncio
async def test_authoritative_snapshot_filters_unavailable_candidates_and_links_attribution(
    monkeypatch, visual_dependencies
):
    hits = [_hit("P1"), _hit("P2")]
    service = VisualProductSearchService()
    monkeypatch.setattr(
        service,
        "_rerank",
        AsyncMock(
            return_value=(
                hits,
                {"candidateCount": 2, "rerankedCount": 2, "degraded": False},
            )
        ),
    )
    monkeypatch.setattr(
        "app.visual.search_service.product_service.load_verified_products",
        AsyncMock(
            return_value=[
                {
                    "product_id": "P2",
                    "product_name": "可售运动鞋",
                    "cover": "cover.jpg",
                    "min_price": 399,
                    "max_price": 399,
                    "total_stock": 8,
                }
            ]
        ),
    )
    decide = AsyncMock(
        side_effect=lambda **kwargs: ShoppingDecisionResult(
            [_offered(product) for product in kwargs["candidates"]],
            "shopping_decision_v2",
            "visual-request-1",
            "visual-ranking-1",
        )
    )
    monkeypatch.setattr(
        "app.visual.search_service.shopping_decision_service.decide",
        decide,
    )
    attribution = AsyncMock()
    monkeypatch.setattr(
        "app.visual.search_service.recommendation_attribution_service.record_impression",
        attribution,
    )
    trace: dict = {"mode": "visual"}

    result = await service._finalize_hits(
        user_id="u1",
        query_text="红色运动鞋",
        query_image=object(),
        hits=hits,
        exact_product_ids={"P1"},
        trace=trace,
        selected_subject=None,
        started=0,
    )

    assert result.product_ids == ["P2"]
    assert result.source_refs[0]["matchType"] == "VISUALLY_SIMILAR"
    assert trace["snapshot"] == {
        "requested": 2,
        "available": 1,
        "filtered": 1,
        "filteredByAuthority": 1,
        "filteredByPreDecisionConstraints": 0,
    }
    assert trace["recommendation"]["productIds"] == ["P2"]
    assert trace["recommendation"]["requestId"]
    card = json.loads(result.assistant_cards)[0]
    assert card["productId"] == "P2"
    assert card["requestId"] == trace["recommendation"]["requestId"]
    assert decide.await_args.kwargs["source"] == "visual_search"
    attribution.assert_awaited_once()


@pytest.mark.asyncio
async def test_authoritative_snapshot_reapplies_category_brand_and_budget_constraints(
    monkeypatch, visual_dependencies
):
    hits = [_hit(f"P{index}") for index in range(1, 5)]
    service = VisualProductSearchService()
    monkeypatch.setattr(
        service,
        "_rerank",
        AsyncMock(
            return_value=(
                hits,
                {"candidateCount": 4, "rerankedCount": 4, "degraded": False},
            )
        ),
    )
    monkeypatch.setattr(
        "app.visual.search_service.product_service.load_verified_products",
        AsyncMock(
            return_value=[
                {
                    "product_id": "P1",
                    "product_name": "耐克运动鞋",
                    "brand": "耐克",
                    "min_price": 399,
                    "max_price": 399,
                    "total_stock": 8,
                },
                {
                    "product_id": "P2",
                    "product_name": "耐克运动鞋高配款",
                    "brand": "耐克",
                    "min_price": 699,
                    "max_price": 699,
                    "total_stock": 8,
                },
                {
                    "product_id": "P3",
                    "product_name": "阿迪达斯运动鞋",
                    "brand": "阿迪达斯",
                    "min_price": 399,
                    "max_price": 399,
                    "total_stock": 8,
                },
                {
                    "product_id": "P4",
                    "product_name": "耐克手机",
                    "brand": "耐克",
                    "min_price": 399,
                    "max_price": 399,
                    "total_stock": 8,
                },
            ]
        ),
    )
    decide = AsyncMock(
        side_effect=lambda **kwargs: ShoppingDecisionResult(
            [
                _offered(product)
                for product in kwargs["candidates"]
                if product.get("product_id") == "P1"
            ],
            "shopping_decision_v2",
            "visual-request-2",
            "visual-ranking-2",
        )
    )
    monkeypatch.setattr(
        "app.visual.search_service.shopping_decision_service.decide",
        decide,
    )
    attribution = AsyncMock()
    monkeypatch.setattr(
        "app.visual.search_service.recommendation_attribution_service.record_impression",
        attribution,
    )
    trace = {
        "mode": "visual",
        "filters": {
            "brands": ["耐克"],
            "brand": "耐克",
            "budgetMax": 500.0,
            "category": "鞋子",
        },
    }

    result = await service._finalize_hits(
        user_id="u1",
        query_text="500 元以内的耐克运动鞋",
        query_image=object(),
        hits=hits,
        exact_product_ids=set(),
        trace=trace,
        selected_subject=None,
        started=0,
    )

    assert result.product_ids == ["P1"]
    assert trace["snapshot"] == {
        "requested": 4,
        "available": 4,
        "filtered": 0,
        "filteredByAuthority": 0,
        "filteredByPreDecisionConstraints": 0,
    }
    assert len(decide.await_args.kwargs["candidates"]) == 4
    assert decide.await_args.kwargs["user_text"] == "500 元以内的耐克运动鞋"
    attribution.assert_awaited_once()


@pytest.mark.asyncio
async def test_exact_image_match_stays_first_after_model_rerank(
    monkeypatch, visual_dependencies
):
    exact = _hit("P-exact", source="exact_hash")
    similar = _hit("P-similar")
    service = VisualProductSearchService()
    monkeypatch.setattr(
        service,
        "_rerank",
        AsyncMock(
            return_value=(
                [similar, exact],
                {"candidateCount": 2, "rerankedCount": 2, "degraded": False},
            )
        ),
    )
    monkeypatch.setattr(
        "app.visual.search_service.product_service.load_verified_products",
        AsyncMock(
            return_value=[
                {
                    "product_id": "P-similar",
                    "product_name": "视觉相似商品",
                    "min_price": 100,
                    "total_stock": 1,
                },
                {
                    "product_id": "P-exact",
                    "product_name": "同图商品",
                    "min_price": 100,
                    "total_stock": 1,
                },
            ]
        ),
    )
    monkeypatch.setattr(
        "app.visual.search_service.shopping_decision_service.decide",
        AsyncMock(
            side_effect=lambda **kwargs: ShoppingDecisionResult(
                [_offered(product) for product in kwargs["candidates"]],
                "shopping_decision_v2",
                "visual-request-3",
                "visual-ranking-3",
            )
        ),
    )
    monkeypatch.setattr(
        "app.visual.search_service.recommendation_attribution_service.record_impression",
        AsyncMock(),
    )
    trace = {"mode": "visual", "filters": {}}

    result = await service._finalize_hits(
        user_id="u1",
        query_text="找同款",
        query_image=object(),
        hits=[exact, similar],
        exact_product_ids={"P-exact"},
        trace=trace,
        selected_subject=None,
        started=0,
    )

    assert result.product_ids == ["P-exact", "P-similar"]
    assert result.source_refs[0]["matchType"] == "EXACT_IMAGE"
    assert trace["rerank"]["exactMatchesPinned"] == 1


@pytest.mark.asyncio
async def test_understanding_fallback_handles_text_retrieval_failure(
    monkeypatch, visual_dependencies
):
    monkeypatch.setattr(
        "app.visual.search_service.visual_provider.describe_product_attributes",
        AsyncMock(
            return_value=(
                "红色运动鞋",
                {"category": "鞋子", "color": "红色"},
            )
        ),
    )
    monkeypatch.setattr(
        "app.visual.search_service.rag_retriever.search_product_keyword_ids",
        AsyncMock(side_effect=RuntimeError("search unavailable")),
    )
    monkeypatch.setattr(
        "app.visual.search_service.rag_retriever.search_product_vector_ids",
        AsyncMock(return_value=[]),
    )
    service = VisualProductSearchService()
    degraded = Mock()
    monkeypatch.setattr(service, "_record_degraded", degraded)

    result = await service._understanding_fallback(
        user_id="u1",
        query_text="帮我找类似商品",
        query_image=type(
            "QueryImage", (), {"data_uri": "data:image/jpeg;base64,AA=="}
        )(),
        trace={"mode": "visual", "filters": {}},
        selected_subject=None,
        started=0,
    )

    assert result.product_ids == []
    assert "识图和降级检索暂时不可用" in result.content
    assert result.retrieval_trace["outcome"] == "VISUAL_CAPABILITY_UNAVAILABLE"
    assert result.retrieval_trace["understandingFallback"]["searchStatus"] == "FAILED"
    degraded.assert_called_once_with(
        "text_search", "VISUAL_TEXT_FALLBACK_UNAVAILABLE"
    )


@pytest.mark.asyncio
async def test_rerank_provider_failure_preserves_deterministic_fusion_order(
    monkeypatch, visual_dependencies
):
    hits = [_hit("P1"), _hit("P2")]
    monkeypatch.setattr(
        "app.visual.search_service.java_internal_client.fetch_product_image",
        AsyncMock(return_value=(_jpeg(), {"content-type": "image/jpeg"})),
    )
    monkeypatch.setattr(
        "app.visual.search_service.visual_provider.rerank",
        AsyncMock(
            side_effect=VisualProviderError(
                "VISUAL_RERANK_TEMPORARILY_UNAVAILABLE", retryable=True
            )
        ),
    )

    ordered, trace = await VisualProductSearchService()._rerank(
        type("QueryImage", (), {"data_uri": "data:image/jpeg;base64,AA=="})(),
        hits,
    )

    assert [hit.product_id for hit in ordered] == ["P1", "P2"]
    assert trace == {
        "candidateCount": 2,
        "rerankedCount": 0,
        "degraded": True,
        "code": "VISUAL_RERANK_TEMPORARILY_UNAVAILABLE",
    }


def test_weighted_rrf_rejects_low_cosine_but_exact_hash_bypasses_threshold():
    exact = _hit("P-exact", cosine=None, source="exact_hash")
    low = _hit("P-low", cosine=0.2)
    accepted = _hit("P-accepted", cosine=0.7)

    merged, trace = _weighted_rrf(
        [exact],
        [low, accepted],
        [],
        [],
        min_cosine=0.45,
    )

    assert {hit.product_id for hit in merged} == {"P-exact", "P-accepted"}
    assert trace["rejectedByCosine"] == 1
    assert trace["minCosineThreshold"] == 0.45
