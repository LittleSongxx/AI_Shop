"""RRF（Reciprocal Rank Fusion）的唯一实现。

历史上三处并行实现各自定义了 K 常量与名次计分公式：

- ``rrf_merge``（本模块，纯 RRF 融合 id 列表）；
- ``app/rag/retriever.py`` 的 ``_rrf_docs`` / ``rrf_score_at_rank``（带证据下限的文档融合）；
- ``app/visual/search_service.py`` 的 ``_weighted_rrf``（带权重、余弦门与融合 trace）。

三者 K 值相同（60）、名次计分公式相同（1/(K+rank)），后收敛到本模块：
K 常量只在 ``app.constants.RRF_K`` 定义，计分公式只有 ``rrf_score_at_rank`` 一份。
带权重的视觉融合因附带余弦门槛与 merge trace，仍保留在 visual 侧，但它与这里
共用 RRF_K 与 rrf_score_at_rank。
"""

from app.constants import RRF_K


def rrf_score_at_rank(rank: int, k: int = RRF_K) -> float:
    """名次 ``rank``（从 1 起）在单一路召回里贡献的 RRF 分。

    用来把"至少进了某一路的前 N 名"这个可读的条件，翻译成阈值能比较的分数。
    """

    return 1.0 / (k + max(int(rank), 1))


def rrf_merge(keyword_ids: list[str], vector_ids: list[str], limit: int) -> list[str]:
    """纯 RRF 融合两路 id 列表，返回融合分降序的前 ``limit`` 个 id。"""

    scores: dict[str, float] = {}
    for ranked in (keyword_ids, vector_ids):
        for i, pid in enumerate(ranked):
            if not pid:
                continue
            scores[pid] = scores.get(pid, 0.0) + rrf_score_at_rank(i + 1)

    sorted_ids = sorted(scores.items(), key=lambda x: (-x[1], x[0]))

    return [pid for pid, _ in sorted_ids[: max(limit, 1)]]
