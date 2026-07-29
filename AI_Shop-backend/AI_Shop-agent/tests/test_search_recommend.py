"""多路召回 + MMR 的行为约束。

这一整条链路（品类加权投票、共同购买召回、MMR 去同质）此前没有任何测试。里面的两个纯函数
`_merge_deduplicate` 和 `_mmr_rerank` 尤其值得先测：它们不需要任何桩就能断言，而且决定了
首页推荐最终摆什么——排序错了用户直接看得见。

测的是行为约束而不是当前输出的快照。比如 MMR 那几条断言的是"同品类连续出现要被打断"，
不是"结果必须等于 [a, c, b]"——后者会在任何一次调参后变红，且红了也不说明哪里坏了。
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.java_internal_client import java_internal_client
from app.services.search_recommend_service import SearchRecommendService


def _product(pid: str, category: str = "", name: str = "") -> dict:
    return {
        "product_id": pid,
        "category_id": category,
        "product_name": name or f"商品{pid}",
    }


# --------------------------------------------------------------------------- #
# _merge_deduplicate                                                          #
# --------------------------------------------------------------------------- #


def test_merge_keeps_primary_order_and_drops_duplicates():
    merge = SearchRecommendService._merge_deduplicate
    primary = [_product("1"), _product("2")]
    secondary = [_product("2"), _product("3")]

    merged = merge(primary, secondary)

    # 品类召回是主序，共同购买只填空位——重复的 "2" 不能把它自己往后挪。
    assert [p["product_id"] for p in merged] == ["1", "2", "3"]


def test_merge_deduplicates_across_the_two_id_spellings():
    """Java 侧同一个字段在不同接口上有 snake_case 和 camelCase 两种拼法。

    去重按拼法而不是按商品去做的话，同一个商品会在首页出现两次。
    """
    merge = SearchRecommendService._merge_deduplicate
    merged = merge([{"product_id": "7"}], [{"productId": "7"}, {"productId": "8"}])

    assert [str(p.get("product_id") or p.get("productId")) for p in merged] == ["7", "8"]


def test_merge_skips_entries_without_any_id():
    """没有 ID 的条目不能进结果：后续 MMR 和前端卡片都按 ID 索引。"""
    merge = SearchRecommendService._merge_deduplicate
    merged = merge([{"product_name": "无 ID"}], [_product("1")])

    assert [p["product_id"] for p in merged] == ["1"]


def test_merge_of_two_empty_lists_is_empty():
    assert SearchRecommendService._merge_deduplicate([], []) == []


# --------------------------------------------------------------------------- #
# _mmr_rerank                                                                 #
# --------------------------------------------------------------------------- #


def test_mmr_breaks_up_a_run_of_one_category():
    """同品类连续排在前面时，MMR 要把后面品类的商品提上来。

    这是这个函数存在的唯一理由：品类召回本身返回的就是同一个品类，直接截断会得到
    一整页一模一样的东西。
    """
    products = [
        _product("1", "phone"),
        _product("2", "phone"),
        _product("3", "phone"),
        _product("4", "snack"),
    ]

    picked = SearchRecommendService._mmr_rerank(products, limit=2)

    assert [p["product_id"] for p in picked] == ["1", "4"]


def test_mmr_always_keeps_the_top_ranked_item():
    """相关性最高的那条不能被多样性挤掉。

    λ=0.7 偏向相关性，首位在任何配置下都应该保留——否则"推荐"就变成"随机换品类"。
    """
    products = [_product(str(i), "phone" if i == 0 else "snack") for i in range(6)]

    picked = SearchRecommendService._mmr_rerank(products, limit=3)

    assert picked[0]["product_id"] == "0"


def test_mmr_returns_exactly_limit_items_when_pool_is_larger():
    products = [_product(str(i), f"cat{i % 3}") for i in range(10)]

    picked = SearchRecommendService._mmr_rerank(products, limit=4)

    assert len(picked) == 4
    assert len({p["product_id"] for p in picked}) == 4


def test_mmr_is_a_noop_when_pool_does_not_exceed_limit():
    """池子不比 limit 大就原样返回——没有可换的东西，重排只会白算一遍。"""
    products = [_product("1", "phone"), _product("2", "phone")]

    assert SearchRecommendService._mmr_rerank(products, limit=5) is products


def test_mmr_tolerates_missing_category():
    """品类缺失时相似度按 0 算，不能抛异常。

    `get_product_detail` 拿不到 category_id 的情况是真会发生的（第三方商品、
    数据补全中），而首页推荐不该因为一条数据不全就整体失败。
    """
    products = [
        _product("1", ""),
        {"product_id": "2"},  # 连 category_id 键都没有
        _product("3", "phone"),
    ]

    picked = SearchRecommendService._mmr_rerank(products, limit=2)

    assert len(picked) == 2


def test_mmr_lambda_one_degenerates_to_plain_relevance_order():
    """λ=1 时多样性项为 0，结果必须退化成原顺序截断。

    这条是给参数本身立约束：如果 λ=1 还在换顺序，说明相关性项算错了。
    """
    products = [_product(str(i), "phone") for i in range(5)]

    picked = SearchRecommendService._mmr_rerank(products, limit=3, lambda_=1.0)

    assert [p["product_id"] for p in picked] == ["0", "1", "2"]


def test_mmr_lambda_zero_maximises_category_spread():
    """λ=0 时只看多样性，同品类的第二条要被排到所有新品类之后。"""
    products = [
        _product("1", "phone"),
        _product("2", "phone"),
        _product("3", "snack"),
        _product("4", "book"),
    ]

    picked = SearchRecommendService._mmr_rerank(products, limit=3, lambda_=0.0)

    categories = [p["category_id"] for p in picked]
    assert len(set(categories)) == 3, f"λ=0 却选出了重复品类：{categories}"


def test_mmr_relevance_decay_eventually_outweighs_diversity():
    """相关性用的是 1/(1+rank)，衰减很快——这决定了多样性的作用范围。

    λ·1/(1+rank) 在 rank 足够深时会小于"同品类但排名靠前"的得分，此后 MMR 就不再
    为了换品类去捞很后面的商品了。这是这个相关性函数的固有性质，不是 bug；写成测试是
    为了让它显式、可复核——换成线性衰减或加大 λ 都会改变这个边界。
    """
    # 前 2 条同品类，之后全是新品类但排名很深。
    products = [_product("1", "phone"), _product("2", "phone")] + [
        _product(str(i), f"cat{i}") for i in range(3, 40)
    ]

    picked = SearchRecommendService._mmr_rerank(products, limit=3, lambda_=0.7)
    ids = [p["product_id"] for p in picked]

    # 第二位仍然会去换品类（此时相关性差距还没拉开）……
    assert ids[1] != "2"
    # ……但捞的是紧邻的那条，不会跳到列表尾部。
    assert int(ids[1]) < 10


# --------------------------------------------------------------------------- #
# _co_purchase_recall                                                         #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_co_purchase_scores_by_combined_rank_across_seeds(monkeypatch):
    """被多个种子商品同时带出来的商品，要排在只被一个种子带出来的前面。

    这是这条召回唯一的"协同"含量所在：如果只按单个列表的名次取，就退化成
    "拿最近买的那一件的关联商品"，多个种子等于白查。
    """
    co_lists = {
        "seed1": ["shared", "only1"],
        "seed2": ["shared", "only2"],
    }

    async def fake_co(pid, limit):
        return co_lists.get(pid, [])

    async def fake_detail(pid):
        return _product(pid, "phone")

    monkeypatch.setattr(java_internal_client, "co_purchase_product_ids", fake_co)
    monkeypatch.setattr(java_internal_client, "get_product_detail", fake_detail)

    service = SearchRecommendService()
    products = await service._co_purchase_recall(["seed1", "seed2"], limit=5)

    assert products[0]["product_id"] == "shared"
    assert {p["product_id"] for p in products} == {"shared", "only1", "only2"}
    assert all(p["_source"] == "co_purchase" for p in products)


@pytest.mark.asyncio
async def test_co_purchase_excludes_what_the_user_already_bought(monkeypatch):
    """已买过的商品不能再推。

    共同购买的定义就是"买了 A 的人也买了 B"，B 里几乎必然包含用户自己那几件。
    """
    async def fake_co(pid, limit):
        return ["seed1", "fresh"]

    async def fake_detail(pid):
        return _product(pid, "phone")

    monkeypatch.setattr(java_internal_client, "co_purchase_product_ids", fake_co)
    monkeypatch.setattr(java_internal_client, "get_product_detail", fake_detail)

    service = SearchRecommendService()
    products = await service._co_purchase_recall(["seed1"], limit=5)

    assert [p["product_id"] for p in products] == ["fresh"]


@pytest.mark.asyncio
async def test_co_purchase_survives_partial_upstream_failure(monkeypatch):
    """一个种子查挂了，其余种子的结果要照常用。

    这条链路一次要发多个内部请求，"有一个失败就整体返回空"会让推荐位在 Java 侧
    抖动时直接空掉。
    """
    async def fake_co(pid, limit):
        if pid == "bad":
            raise RuntimeError("upstream 500")
        return ["ok1"]

    async def fake_detail(pid):
        return _product(pid, "phone")

    monkeypatch.setattr(java_internal_client, "co_purchase_product_ids", fake_co)
    monkeypatch.setattr(java_internal_client, "get_product_detail", fake_detail)

    service = SearchRecommendService()
    products = await service._co_purchase_recall(["bad", "good"], limit=5)

    assert [p["product_id"] for p in products] == ["ok1"]


@pytest.mark.asyncio
async def test_co_purchase_skips_products_whose_detail_is_missing(monkeypatch):
    """详情拿不到的商品不能进结果——卡片渲染需要名称和封面。"""
    async def fake_co(pid, limit):
        return ["has_detail", "no_detail"]

    async def fake_detail(pid):
        return None if pid == "no_detail" else _product(pid, "phone")

    monkeypatch.setattr(java_internal_client, "co_purchase_product_ids", fake_co)
    monkeypatch.setattr(java_internal_client, "get_product_detail", fake_detail)

    service = SearchRecommendService()
    products = await service._co_purchase_recall(["seed"], limit=5)

    assert [p["product_id"] for p in products] == ["has_detail"]


@pytest.mark.asyncio
async def test_co_purchase_without_purchase_history_makes_no_calls(monkeypatch):
    """没有购买历史时不该发任何请求——新用户是最常见的情况。"""
    called = False

    async def fake_co(pid, limit):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(java_internal_client, "co_purchase_product_ids", fake_co)

    service = SearchRecommendService()
    assert await service._co_purchase_recall([], limit=5) == []
    assert not called


# --------------------------------------------------------------------------- #
# _resolve_category_from_browse                                               #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_purchase_history_outweighs_browsing_in_category_vote(monkeypatch):
    """买过的品类权重是浏览的两倍。

    浏览 2 件 snack、买过 1 件 phone 时应当选 phone：2×1 vs 2×1 打平，靠插入顺序
    （购买先入）让购买胜出。再多浏览一件就该翻盘，见下一条。
    """
    monkeypatch.setattr(
        java_internal_client, "browse_history_ids",
        AsyncMock(return_value=["s1", "s2"]),
    )
    monkeypatch.setattr(
        java_internal_client, "purchase_history_product_ids",
        AsyncMock(return_value=["p1"]),
    )

    async def fake_detail(pid):
        return _product(pid, "phone" if pid == "p1" else "snack")

    monkeypatch.setattr(java_internal_client, "get_product_detail", fake_detail)

    service = SearchRecommendService()
    assert await service._resolve_category_from_browse("u1") == "phone"


@pytest.mark.asyncio
async def test_enough_browsing_can_outvote_a_single_purchase(monkeypatch):
    """浏览量够大时可以盖过一次购买——权重是 2:1，不是"购买一票否决"。"""
    monkeypatch.setattr(
        java_internal_client, "browse_history_ids",
        AsyncMock(return_value=["s1", "s2", "s3"]),
    )
    monkeypatch.setattr(
        java_internal_client, "purchase_history_product_ids",
        AsyncMock(return_value=["p1"]),
    )

    async def fake_detail(pid):
        return _product(pid, "phone" if pid == "p1" else "snack")

    monkeypatch.setattr(java_internal_client, "get_product_detail", fake_detail)

    service = SearchRecommendService()
    assert await service._resolve_category_from_browse("u1") == "snack"


@pytest.mark.asyncio
async def test_a_product_in_both_histories_counts_once_at_the_higher_weight(monkeypatch):
    """同一商品既浏览又购买时按购买权重算一次，不能 2+1 累加成 3。

    累加会让"买完又回来看一眼"的商品品类被不成比例地放大。
    """
    monkeypatch.setattr(
        java_internal_client, "browse_history_ids", AsyncMock(return_value=["same"]),
    )
    monkeypatch.setattr(
        java_internal_client, "purchase_history_product_ids",
        AsyncMock(return_value=["same"]),
    )

    seen: list[str] = []

    async def fake_detail(pid):
        seen.append(pid)
        return _product(pid, "phone")

    monkeypatch.setattr(java_internal_client, "get_product_detail", fake_detail)

    service = SearchRecommendService()
    assert await service._resolve_category_from_browse("u1") == "phone"
    # 去重发生在取详情之前，所以只查一次。
    assert seen == ["same"]


@pytest.mark.asyncio
async def test_category_resolution_falls_back_to_latest_browse(monkeypatch):
    """批量浏览历史为空时回落到单条最近浏览。"""
    monkeypatch.setattr(
        java_internal_client, "browse_history_ids", AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        java_internal_client, "purchase_history_product_ids", AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        java_internal_client, "latest_browse_product_id", AsyncMock(return_value="last"),
    )
    monkeypatch.setattr(
        java_internal_client, "get_product_detail",
        AsyncMock(return_value=_product("last", "book")),
    )

    service = SearchRecommendService()
    assert await service._resolve_category_from_browse("u1") == "book"


@pytest.mark.asyncio
async def test_category_resolution_returns_none_without_any_signal(monkeypatch):
    """完全没有行为数据时返回 None，由上层走热销兜底。"""
    monkeypatch.setattr(
        java_internal_client, "browse_history_ids", AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        java_internal_client, "purchase_history_product_ids", AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        java_internal_client, "latest_browse_product_id", AsyncMock(return_value=None),
    )

    service = SearchRecommendService()
    assert await service._resolve_category_from_browse("u1") is None


@pytest.mark.asyncio
async def test_anonymous_user_short_circuits(monkeypatch):
    """没有 user_id 时不发请求。推荐位在未登录页面上也会渲染。"""
    called = False

    async def boom(*args, **kwargs):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(java_internal_client, "browse_history_ids", boom)

    service = SearchRecommendService()
    assert await service._resolve_category_from_browse("") is None
    assert not called


# --------------------------------------------------------------------------- #
# load_recommend_products：两路都空时的兜底                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_recommend_falls_back_to_hot_sale_when_both_recalls_are_empty(monkeypatch):
    """两路召回都空必须回落热销，不能把空列表交给前端。

    新用户 + 无购买历史是最常见的入口状态，这条路径的覆盖率比主路径更重要。
    """
    service = SearchRecommendService()
    monkeypatch.setattr(
        service, "_resolve_category_from_browse", AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        java_internal_client, "purchase_history_product_ids", AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(service, "_co_purchase_recall", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        service, "_load_hot_sale", AsyncMock(return_value=[_product("hot", "phone")]),
    )

    products = await service.load_recommend_products("u1", limit=8)

    assert [p["product_id"] for p in products] == ["hot"]


@pytest.mark.asyncio
async def test_recommend_survives_both_recall_paths_raising(monkeypatch):
    """两路召回都抛异常时仍然返回热销，而不是把异常抛给接口。"""
    service = SearchRecommendService()
    monkeypatch.setattr(
        service, "_resolve_category_from_browse",
        AsyncMock(side_effect=RuntimeError("category recall down")),
    )
    monkeypatch.setattr(
        java_internal_client, "purchase_history_product_ids",
        AsyncMock(side_effect=RuntimeError("history down")),
    )
    monkeypatch.setattr(
        service, "_load_hot_sale", AsyncMock(return_value=[_product("hot", "phone")]),
    )

    products = await service.load_recommend_products("u1", limit=8)

    assert [p["product_id"] for p in products] == ["hot"]
