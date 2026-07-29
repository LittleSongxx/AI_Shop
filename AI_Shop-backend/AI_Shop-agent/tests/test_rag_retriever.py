import asyncio
from unittest.mock import AsyncMock

import pytest

from app.config.settings import Settings, get_settings
from app.rag.retriever import (
    ES_MAX_NUM_CANDIDATES,
    RRF_RANK_CONSTANT,
    RagRetriever,
    cosine_to_es_score,
    knn_num_candidates,
    rrf_score_at_rank,
)


@pytest.mark.asyncio
async def test_exact_faq_fast_path_returns_traceable_source(monkeypatch):
    retriever = RagRetriever()
    monkeypatch.setattr(retriever, "_knowledge_version", AsyncMock(return_value=7))
    monkeypatch.setattr(
        retriever,
        "_exact_faq",
        AsyncMock(
            return_value={
                "question": "发票在哪里申请",
                "answer": "请在订单详情页申请发票。",
                "question_id": 12,
                "category": "invoice",
                "source": "ADMIN",
            }
        ),
    )

    result = await retriever.exact_faq_answer("发票在哪里申请？")

    assert result == {
        "answer": "请在订单详情页申请发票。",
        "question": "发票在哪里申请",
        "questionId": 12,
        "category": "invoice",
        "source": "ADMIN",
        "version": 7,
    }


@pytest.mark.asyncio
async def test_exact_faq_fast_path_times_out_without_blocking_chat(monkeypatch):
    monkeypatch.setenv("FAQ_FAST_PATH_TIMEOUT_SECONDS", "0.01")
    get_settings.cache_clear()
    retriever = RagRetriever()
    monkeypatch.setattr(retriever, "_knowledge_version", AsyncMock(return_value=1))

    async def slow_exact(*_args):
        await asyncio.sleep(0.1)
        return {"answer": "不应返回"}

    monkeypatch.setattr(retriever, "_exact_faq", slow_exact)
    try:
        assert await retriever.exact_faq_answer("配送范围是什么") is None
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_hybrid_search_returns_bounded_source_trace(monkeypatch):
    retriever = RagRetriever()
    monkeypatch.setattr(retriever, "_knowledge_version", AsyncMock(return_value=9))
    monkeypatch.setattr(retriever, "_exact_faq", AsyncMock(return_value=None))
    monkeypatch.setattr(
        retriever,
        "_search_knowledge_docs",
        AsyncMock(
            return_value=[
                {
                    "id": "knowledge_7_1_0",
                    "content": "配送范围覆盖全国大部分地区。",
                    "metadata": {
                        "dataType": "knowledge",
                        "documentId": "7",
                        "chunkId": "knowledge_7_1_0",
                        "title": "配送规则",
                        "source": "配送说明",
                        "version": 1,
                        "heading": "配送范围",
                    },
                    "score": 0.91,
                    "source": "rerank",
                }
            ],
        ),
    )

    result = await retriever.search_faq_with_trace("配送范围")

    assert "配送范围覆盖全国" in result["text"]
    assert result["trace"]["mode"] == "hybrid"
    assert result["trace"]["knowledgeVersion"] == 9
    assert result["source_refs"][0]["documentId"] == "7"
    assert result["source_refs"][0]["chunkId"] == "knowledge_7_1_0"
    assert result["source_refs"][0]["retrieval"] == "rerank"


def test_knn_num_candidates_keeps_a_recall_floor_and_respects_es_bounds():
    settings = get_settings()

    # At the default k the floor wins over the multiple, so the HNSW walk gets a
    # usable candidate pool instead of the bare 2x the old code sent.
    assert knn_num_candidates(15, settings) == settings.knn_num_candidates_min

    # Above the floor the multiple takes over.
    assert knn_num_candidates(200, settings) == 200 * settings.knn_num_candidates_factor

    # ES rejects num_candidates above 10000, and requires num_candidates >= k.
    assert knn_num_candidates(9000, settings) == ES_MAX_NUM_CANDIDATES
    assert knn_num_candidates(1, settings) >= 1


# ---------------------------------------------------------------------------
# 检索阈值的量纲
#
# 这一组测的不是"取值调得好不好"（那要在真实 ES 上标定），而是"阈值有没有被用在
# 它自己的量纲上"。原先一个 rag_score_threshold=0.5 同时比 ES cosine 打分、BM25
# 原始分和 rerank 归一分，其中至少两处必然失真。
# ---------------------------------------------------------------------------


def test_cosine_threshold_is_not_the_same_number_as_es_score():
    """0.5 的 ES ``_score`` 等于 cos=0，这正是原先那道向量阈值几乎不过滤的原因。"""
    assert cosine_to_es_score(0.0) == 0.5
    assert cosine_to_es_score(1.0) == 1.0
    assert cosine_to_es_score(-1.0) == 0.0
    # 原先商品召回写死 threshold=0.4，看着像"要求四成相似"，实际是 cos>=-0.2。
    assert cosine_to_es_score(-0.2) == pytest.approx(0.4)
    # 现在配的 cosine 下限换算出来必须严格高于中性点，否则等于没设。
    settings = get_settings()
    assert cosine_to_es_score(settings.rag_vector_min_cosine) > 0.5
    assert cosine_to_es_score(settings.rag_product_vector_min_cosine) > 0.5


def test_vector_search_filters_on_cosine_not_raw_es_score(monkeypatch):
    """``min_cosine=0.3`` 必须筛掉 cos=0.1 的命中。

    按老的语义（拿 0.3 直接比 ``_score``）它会被留下来，因为 cos=0.1 的 ``_score``
    是 0.55。
    """
    retriever = RagRetriever()
    captured: dict = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "hits": {
                    "hits": [
                        {"_id": "hi", "_score": cosine_to_es_score(0.62),
                         "_source": {"content": "相关", "metadata": {}}},
                        {"_id": "lo", "_score": cosine_to_es_score(0.10),
                         "_source": {"content": "边缘", "metadata": {}}},
                    ]
                }
            }

    class _Client:
        async def post(self, url, **kwargs):
            captured["body"] = kwargs.get("json")
            return _Resp()

    async def fake_client(*args, **kwargs):
        return _Client()

    monkeypatch.setattr("app.rag.retriever.get_client", fake_client)
    monkeypatch.setattr("app.rag.retriever.embed_text", AsyncMock(return_value=[0.1] * 1024))

    docs = asyncio.run(
        retriever._vector_search("查询", "knowledge", top_k=5, min_cosine=0.3)
    )

    assert [doc["id"] for doc in docs] == ["hi"]


def test_rrf_keeps_fusion_score_and_preserves_engine_score():
    """融合后 ``score`` 必须是 RRF 分，不能被原始 BM25 分覆盖。

    原先写 ``max(原始分, RRF分)``：BM25 的 ``_score`` 是 1~20、RRF 分最大约 0.033，
    max 永远取原始分，融合结果被自己覆盖，trace 里的 topScore 也变成跨查询不可比的值。
    """
    retriever = RagRetriever()
    bm25 = [{"id": "a", "score": 14.2, "content": "x"}, {"id": "b", "score": 8.7, "content": "y"}]
    vector = [{"id": "b", "score": 0.83, "content": "y"}, {"id": "c", "score": 0.71, "content": "z"}]

    merged = retriever._rrf_docs([bm25, vector], limit=3)

    # b 被两路都召回，所以 RRF 分最高——即使它的 BM25 分低于 a。
    assert [doc["id"] for doc in merged][0] == "b"
    assert merged[0]["score"] == pytest.approx(
        rrf_score_at_rank(2) + rrf_score_at_rank(1)
    )
    # 每一条的 score 都是 RRF 量纲，不再混着 1~20 的 BM25 分。
    assert all(doc["score"] < 0.05 for doc in merged)
    # 原始分没丢，排查单路召回质量时还要用。
    assert {doc["id"]: doc["engineScore"] for doc in merged}["a"] == 14.2


def test_evidence_gate_uses_the_scale_that_produced_the_score():
    """同一个 0.5 不能同时判 rerank 归一分和 RRF 融合分。"""
    retriever = RagRetriever()

    # rerank 之后：0~1 归一相关性，0.5 这个绝对阈值在这里才说得通。
    assert retriever._has_enough_evidence([{"score": 0.62, "source": "rerank"}])
    assert not retriever._has_enough_evidence([{"score": 0.31, "source": "rerank"}])

    # 没有 rerank（未配 key 或熔断）：RRF 分永远远小于 0.5，拿 0.5 判会把所有证据
    # 都判成不足；按名次判才有意义。
    settings = get_settings()
    rank_floor = settings.rag_evidence_min_rrf_rank
    assert retriever._has_enough_evidence(
        [{"score": rrf_score_at_rank(1), "source": "rrf"}]
    )
    assert retriever._has_enough_evidence(
        [{"score": rrf_score_at_rank(rank_floor), "source": "rrf"}]
    )
    assert not retriever._has_enough_evidence(
        [{"score": rrf_score_at_rank(rank_floor + 1), "source": "rrf"}]
    )

    assert not retriever._has_enough_evidence([])


def test_evidence_gate_is_not_trivially_true_for_bm25_scores():
    """回归守卫：BM25 原始分不该再让闸门恒为真。

    这是原缺陷的直接形态——``_rrf_docs`` 把 BM25 的 ``_score``（1~20）留在 ``score``
    上，任何命中都 >= 0.5，于是"证据是否充分"这道判断从来没有否过。
    """
    retriever = RagRetriever()
    docs = retriever._rrf_docs(
        [[{"id": "a", "score": 14.2, "content": "x"}]], limit=1
    )
    # 单路、名次很靠前的情况下仍然算证据充分（这是对的）……
    assert retriever._has_enough_evidence(docs)
    # ……但分数本身已经不是那个 14.2 了，闸门判的是名次。
    assert docs[0]["score"] < 0.05


def test_rrf_rank_constant_matches_the_reference_implementation():
    """60 是 RRF 论文和 ES rrf retriever 的取值；改了它所有 RRF 阈值都要重标。"""
    assert RRF_RANK_CONSTANT == 60
    assert rrf_score_at_rank(1) == pytest.approx(1 / 61)


def test_rag_thresholds_are_independently_configurable():
    """四个阈值必须能各自改动，不能又被合成一个。"""
    settings = Settings(
        rag_vector_min_cosine=0.55,
        rag_product_vector_min_cosine=0.15,
        rag_evidence_min_relevance=0.8,
        rag_evidence_min_rrf_rank=3,
    )
    assert settings.rag_vector_min_cosine == 0.55
    assert settings.rag_product_vector_min_cosine == 0.15
    assert settings.rag_evidence_min_relevance == 0.8
    assert settings.rag_evidence_min_rrf_rank == 3
