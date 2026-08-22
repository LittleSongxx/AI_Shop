from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.rag.evidence_selector import select_minimal_evidence
from app.rag.query_planner import plan_rag_query, query_fact_hints
from app.rag.retriever import (
    RagRetriever,
    _promote_canonical_hint_docs,
    _rerank_query_with_fact_hints,
    rerank_evaluation_scope,
)


def _doc(doc_id: str, domain: str = "LOGISTICS") -> dict:
    return {
        "id": doc_id,
        "content": "用户可以在订单详情查看物流。",
        "metadata": {
            "dataType": "knowledge",
            "source": "08-logistics-exceptions-and-receipt.md",
            "heading": "查看物流",
            "domain": domain,
        },
        "score": 0.9,
        "source": "bm25",
    }


def test_canonical_fact_hint_survives_cross_variant_rrf_truncation():
    target = {
        "id": "target",
        "content": "模拟物流不构成到达时间 SLA。",
        "metadata": {
            "dataType": "knowledge",
            "source": "08-logistics-exceptions-and-receipt.md",
            "heading": "模拟物流边界",
            "domain": "LOGISTICS",
        },
        "score": 0.01,
        "source": "rrf",
    }
    unrelated = [_doc(f"other-{index}") for index in range(3)]

    selected, promoted = _promote_canonical_hint_docs(
        [[*unrelated, target]],
        unrelated,
        fact_hints=["logistics.simulated_no_sla"],
        limit=3,
    )

    assert promoted == ["target"]
    assert [row["id"] for row in selected] == ["target", "other-0", "other-1"]


def test_rerank_query_uses_trusted_fact_title_not_evaluation_label():
    query = _rerank_query_with_fact_hints(
        "平台是否承诺两小时送达",
        ["logistics.simulated_no_sla"],
    )

    assert query.startswith("平台是否承诺两小时送达\n检索目标：")
    assert "模拟物流边界" in query
    assert "优先直接回答、约束或明确否定" in query


def test_query_fact_hints_are_runtime_business_rules_not_eval_labels():
    assert query_fact_hints("加购价是否保证最终成交价") == (
        "cart.price_snapshot_not_guarantee",
    )
    assert query_fact_hints("AI能直接退款吗") == (
        "ai.capability_and_confirmation",
    )
    assert query_fact_hints("会员等级由什么数值和门槛决定") == (
        "member.growth.thresholds",
    )
    assert query_fact_hints("演示物流轨迹能否作为真实时效承诺") == (
        "logistics.simulated_no_sla",
    )


def test_query_fact_hints_do_not_claim_unsupported_details():
    assert query_fact_hints("连续签到中断后连续天数怎样计算") == ()
    assert query_fact_hints("转人工时系统会附带哪些排查上下文") == ()


def test_query_fact_hints_distinguish_ai_actor_from_ai_data_deletion():
    assert query_fact_hints("AI能直接退款吗") == (
        "ai.capability_and_confirmation",
    )
    assert query_fact_hints("删除AI数据会把支付记录删除吗") == (
        "privacy.retained_business_anonymization",
    )


def test_query_fact_hints_distinguish_cart_snapshot_from_checkout_revalidation():
    assert query_fact_hints("购物车价格是最终成交价吗") == (
        "cart.price_snapshot_not_guarantee",
    )
    assert query_fact_hints("提交订单时为什么价格可能与购物车不同") == (
        "checkout.current_product_revalidation",
    )


def test_query_fact_hints_select_stock_fact_for_cart_price_and_stock_question():
    assert query_fact_hints("加入购物车的价格和库存到结算时还会重新检查吗？") == (
        "checkout.price_and_stock_revalidation",
    )
    # A generic replay question remains on the established checkout fact so
    # equivalent canonical references are not needlessly mixed.
    assert query_fact_hints("结算时会重新检查商品价格库存吗？") == (
        "checkout.current_product_revalidation",
    )


def test_query_fact_hints_cover_address_snapshot_and_refund_channel_queries():
    assert query_fact_hints("下单后修改地址簿会自动改掉已有订单地址吗？") == (
        "address.order_snapshot",
    )
    assert query_fact_hints("申请退货退款应从订单详情的哪个入口开始？") == (
        "aftersales.request_and_refund_boundary",
    )


def test_evidence_selector_promotes_preferred_fact_beyond_top_two_candidates():
    docs = [
        {"content": "普通地址说明"},
        {"content": "默认地址说明"},
        {"content": "订单快照说明"},
    ]
    refs = [
        {"source": "07-account-address-and-security.md", "heading": "收货地址管理"},
        {"source": "07-account-address-and-security.md", "heading": "默认地址"},
        {"source": "07-account-address-and-security.md", "heading": "下单前确认"},
    ]

    selection = select_minimal_evidence(
        docs,
        refs,
        preferred_fact_ids=["address.order_snapshot"],
        max_items=1,
    )

    assert selection.items[0].fact_ids == ("address.order_snapshot",)
    assert selection.items[0].text == "订单快照说明"


def test_query_fact_hints_cover_runtime_business_propositions():
    assert query_fact_hints("知识检索找不到充分依据时平台要求助手怎么做") == (
        "rag.retrieval_and_abstention",
    )
    assert query_fact_hints("知识库证据不够时助手应该怎样回答") == (
        "rag.retrieval_and_abstention",
    )
    assert query_fact_hints("RAG检索不足时的grounding含义是什么") == (
        "rag.retrieval_and_abstention",
    )
    assert query_fact_hints("知识检索得到的证据互相矛盾时怎么办") == (
        "rag.retrieval_and_abstention",
    )
    assert query_fact_hints("售后重复提交会创建多个申请吗") == (
        "aftersales.submit_idempotently",
    )
    assert query_fact_hints("演示环境会不会真正扣除银行卡资金") == (
        "payment.demo_no_real_funds",
    )
    assert query_fact_hints("已发布规则不足时知识助手应该给确定答案吗") == (
        "rag.retrieval_and_abstention",
    )
    assert query_fact_hints("同一幂等键换了结算内容会怎样处理") == (
        "checkout.idempotency_key",
    )
    assert query_fact_hints("物流长时间不更新时应提供哪些信息给客服") == (
        "logistics.delayed_event_support",
    )
    assert query_fact_hints("AI写操作前要确认吗") == (
        "ai.capability_and_confirmation",
    )
    assert query_fact_hints("平台是否承诺所有订单两小时内送达") == (
        "logistics.simulated_no_sla",
    )


@pytest.mark.asyncio
async def test_clear_dual_channel_query_skips_llm_expansion(monkeypatch):
    retriever = RagRetriever()
    monkeypatch.setattr(retriever, "_keyword_search_docs", AsyncMock(return_value=[_doc("a")]))
    monkeypatch.setattr(retriever, "_vector_search", AsyncMock(return_value=[_doc("a")]))
    expansion = AsyncMock(return_value=["物流查询", "订单物流"])
    monkeypatch.setattr("app.rag.retriever.expand_query", expansion)

    result = await retriever._search_knowledge_docs(
        "物流查询",
        10,
        version_filter=1,
        active_document_ids=["1"],
        planned_query=plan_rag_query("物流查询"),
    )

    assert result
    expansion.assert_not_awaited()


@pytest.mark.asyncio
async def test_multi_domain_query_expands_at_most_once(monkeypatch):
    retriever = RagRetriever()
    monkeypatch.setattr(retriever, "_keyword_search_docs", AsyncMock(return_value=[_doc("a")]))
    monkeypatch.setattr(retriever, "_vector_search", AsyncMock(return_value=[_doc("a")]))
    monkeypatch.setattr(retriever, "_rerank", AsyncMock(return_value=[dict(_doc("a"), source="rerank")]))
    expansion = AsyncMock(return_value=["物流查询", "优惠券使用"])
    monkeypatch.setattr("app.rag.retriever.expand_query", expansion)

    await retriever._search_knowledge_docs(
        "查询物流，同时说明优惠券怎么使用",
        10,
        version_filter=1,
        active_document_ids=["1"],
        planned_query=plan_rag_query("查询物流，同时说明优惠券怎么使用"),
    )

    expansion.assert_awaited_once()


@pytest.mark.asyncio
async def test_optional_rerank_fallback_keeps_bounded_rrf_candidates(monkeypatch):
    retriever = RagRetriever()
    docs = [_doc("a"), _doc("b")]
    monkeypatch.setattr(retriever, "_keyword_search_docs", AsyncMock(return_value=docs))
    monkeypatch.setattr(retriever, "_vector_search", AsyncMock(return_value=docs))
    monkeypatch.setattr(retriever, "_rerank", AsyncMock(return_value=[dict(docs[0], source="rrf")]))

    result = await retriever._search_knowledge_docs(
        "查询物流",
        10,
        version_filter=1,
        active_document_ids=["1"],
        planned_query=plan_rag_query("查询物流"),
    )

    assert [doc["id"] for doc in result] == ["a"]


@pytest.mark.asyncio
async def test_required_rerank_fallback_fails_closed(monkeypatch):
    retriever = RagRetriever()
    docs = [_doc("a"), _doc("b")]
    monkeypatch.setattr(retriever, "_keyword_search_docs", AsyncMock(return_value=docs))
    monkeypatch.setattr(retriever, "_vector_search", AsyncMock(return_value=docs))
    monkeypatch.setattr(
        retriever,
        "_rerank",
        AsyncMock(return_value=[dict(docs[0], source="rrf")]),
    )
    monkeypatch.setattr(
        "app.rag.retriever.get_settings",
        lambda: SimpleNamespace(
            rerank_required=True,
            rerank_top_n=6,
            rag_top_k=10,
        ),
    )

    result = await retriever._search_knowledge_docs(
        "查询物流",
        10,
        version_filter=1,
        active_document_ids=["1"],
        planned_query=plan_rag_query("查询物流"),
    )

    assert result == []


@pytest.mark.asyncio
async def test_evaluation_rerank_fallback_fails_closed_even_when_optional(monkeypatch):
    retriever = RagRetriever()
    docs = [_doc("a"), _doc("b")]
    monkeypatch.setattr(retriever, "_keyword_search_docs", AsyncMock(return_value=docs))
    monkeypatch.setattr(retriever, "_vector_search", AsyncMock(return_value=docs))
    monkeypatch.setattr(
        retriever,
        "_rerank",
        AsyncMock(return_value=[dict(docs[0], source="rrf")]),
    )

    with rerank_evaluation_scope():
        result = await retriever._search_knowledge_docs(
            "查询物流",
            10,
            version_filter=1,
            active_document_ids=["1"],
            planned_query=plan_rag_query("查询物流"),
        )

    assert result == []
