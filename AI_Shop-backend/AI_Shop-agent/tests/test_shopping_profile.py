import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.constants import CLARIFY_MAX_TEXT_LENGTH
from app.services.shopping_profile_service import (
    ShoppingProfileService,
    empty_profile,
    extract_profile,
    merge_profiles,
    prune_expired_profile,
)


def _db_cursor(fetchone_result=None):
    """构造一个可注入的 acquire() 上下文，返回 DictCursor 形状的行。"""
    cur = AsyncMock()
    cur.fetchone = AsyncMock(return_value=fetchone_result)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=cur)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cur, cm


def test_extract_profile_parses_budget_brand_category_and_preferences():
    profile = extract_profile("想买华为手机，预算3000到5000，办公续航，不要苹果")

    assert profile["category"] == "手机"
    assert profile["budgetMin"] == 3000
    assert profile["budgetMax"] == 5000
    assert profile["brands"] == ["华为"]
    assert profile["excludedBrands"] == ["苹果"]
    assert profile["scenarios"] == ["办公"]
    assert profile["features"] == ["续航"]


@pytest.mark.asyncio
async def test_explicit_profile_fields_get_governed_ttl_and_message_source(monkeypatch):
    service = ShoppingProfileService()
    monkeypatch.setattr(service, "get_profile", AsyncMock(return_value=empty_profile()))
    monkeypatch.setattr(service, "_save_redis", AsyncMock())
    monkeypatch.setattr(service, "_save_db", AsyncMock())

    profile = await service.update_profile(
        "u1",
        "3000以内的华为手机，办公续航",
        source_message_id=77,
    )

    category_meta = profile["fieldMeta"]["category"]
    brand_meta = profile["fieldMeta"]["brands"]
    category_expiry = datetime.fromisoformat(
        category_meta["expiresAt"].replace("Z", "+00:00")
    )
    brand_expiry = datetime.fromisoformat(
        brand_meta["expiresAt"].replace("Z", "+00:00")
    )
    assert category_meta["source"] == "EXPLICIT_CHAT"
    assert category_meta["sourceMessageId"] == 77
    assert timedelta(days=59) < brand_expiry - category_expiry < timedelta(days=61)
    assert profile["revision"] == 1


@pytest.mark.asyncio
async def test_automatic_profile_update_retries_on_revision_conflict(monkeypatch):
    service = ShoppingProfileService()
    stale = {**empty_profile(), "revision": 1, "category": "手机"}
    latest = {
        **empty_profile(),
        "revision": 2,
        "category": "手机",
        "brands": ["华为"],
    }
    save_db = AsyncMock(side_effect=[False, True])
    save_redis = AsyncMock()
    monkeypatch.setattr(service, "get_profile", AsyncMock(return_value=stale))
    monkeypatch.setattr(service, "_load_from_db", AsyncMock(return_value=latest))
    monkeypatch.setattr(service, "_save_db", save_db)
    monkeypatch.setattr(service, "_save_redis", save_redis)

    updated = await service.update_profile(
        "u1", "预算5000元的手机", source_message_id=88
    )

    assert save_db.await_count == 2
    assert updated["revision"] == 3
    assert updated["brands"] == ["华为"]
    assert updated["budgetMax"] == 5000
    save_redis.assert_awaited_once_with("u1", updated)


def test_expired_fields_are_removed_independently():
    now = datetime(2026, 8, 7, tzinfo=timezone.utc)
    profile = {
        **empty_profile(),
        "category": "手机",
        "brands": ["华为"],
        "fieldMeta": {
            "category": {"expiresAt": (now - timedelta(seconds=1)).isoformat()},
            "brands": {"expiresAt": (now + timedelta(days=1)).isoformat()},
        },
    }

    pruned = prune_expired_profile(profile, now=now)

    assert pruned["category"] is None
    assert pruned["brands"] == ["华为"]
    assert "category" not in pruned["fieldMeta"]


def test_partial_manual_patch_is_validated_against_existing_values():
    profile = {**empty_profile(), "budgetMin": 3000, "budgetMax": 5000}
    profile["budgetMax"] = 2000

    with pytest.raises(ValueError, match="budgetMin"):
        ShoppingProfileService._validate_profile_values(profile)


def test_extract_profile_supports_units_and_upper_budget():
    profile = extract_profile("推荐3k以内的轻薄笔记本")

    assert profile["budgetMin"] is None
    assert profile["budgetMax"] == 3000
    assert profile["category"] == "笔记本电脑"
    assert profile["features"] == ["便携"]


def test_merge_profile_keeps_previous_constraints():
    current = extract_profile("预算3000以内的华为手机")
    incoming = extract_profile("办公、续航，排除苹果")
    merged = merge_profiles(current, incoming)

    assert merged["budgetMax"] == 3000
    assert merged["brands"] == ["华为"]
    assert merged["excludedBrands"] == ["苹果"]
    assert merged["scenarios"] == ["办公"]
    assert merged["features"] == ["续航"]


def test_later_brand_statement_overrides_previous_preference_or_exclusion():
    excluded = merge_profiles(extract_profile("想买华为手机"), extract_profile("不要华为"))
    preferred = merge_profiles(extract_profile("不要苹果"), extract_profile("苹果手机也可以"))

    assert excluded["brands"] == []
    assert excluded["excludedBrands"] == ["华为"]
    assert preferred["brands"] == ["苹果"]
    assert preferred["excludedBrands"] == []


def test_generic_request_requires_clarification_without_profile():
    service = ShoppingProfileService()
    profile = empty_profile()

    assert service.should_clarify("推荐几个商品", "推荐几个商品", profile, None)
    # A considered purchase with only a category is worth one clarifying turn:
    # "手机" alone cannot rank a shelf, budget and scenario can.
    assert service.should_clarify("推荐手机", "推荐手机", profile, None)
    # Low-consideration categories stay direct — no budget interview for snacks.
    assert not service.should_clarify("我要吃零食", "零食", profile, None)


def test_narrowing_signal_suppresses_clarification():
    service = ShoppingProfileService()
    empty = empty_profile()

    # Budget, brand, scenario or feature in the request itself is enough to rank on.
    assert not service.should_clarify("3000以内的手机", "手机", empty, None)
    assert not service.should_clarify("推荐华为手机", "华为手机", empty, None)
    assert not service.should_clarify("办公用的笔记本", "笔记本", empty, None)
    assert not service.should_clarify("轻薄的笔记本", "笔记本", empty, None)

    # A remembered budget also suppresses it, so we never ask the same thing twice.
    remembered = extract_profile("预算3000以内")
    assert not service.should_clarify("推荐手机", "推荐手机", remembered, None)

    # A remembered category alone must NOT suppress it — that was the old bug.
    category_only = extract_profile("买个手机")
    assert service.should_clarify("推荐手机", "推荐手机", category_only, None)


def test_long_request_skips_clarification():
    service = ShoppingProfileService()
    profile = empty_profile()

    # Past the length gate the user has spelled out their own intent.
    long_request = (
        "我最近想换一台手机不过还没决定好到底要选哪个品牌和什么价位"
        "你先随便给我看看有哪些款式我再慢慢挑吧"
    )
    assert len(long_request) > CLARIFY_MAX_TEXT_LENGTH
    assert not service.should_clarify(long_request, "手机", profile, None)


def test_consult_context_skips_clarification():
    service = ShoppingProfileService()
    profile = empty_profile()

    # Looking at a product already supplies the context a question would ask for.
    assert not service.should_clarify(
        "推荐手机",
        "推荐手机",
        profile,
        {"productId": "1", "productName": "某品牌手机"},
    )


def test_filter_products_applies_budget_and_brand_constraints():
    service = ShoppingProfileService()
    profile = extract_profile("预算3000以内的华为手机，不要苹果")
    products = [
        {"product_id": "1", "product_name": "华为轻薄手机", "min_price": 2499, "max_price": 2999, "status": 1},
        {"product_id": "2", "product_name": "华为旗舰手机", "min_price": 3999, "max_price": 4999, "status": 1},
        {"product_id": "3", "product_name": "苹果手机", "min_price": 1999, "max_price": 2999, "status": 1},
    ]

    kept = service.filter_products(products, profile)

    assert [item["product_id"] for item in kept] == ["1"]
    assert "符合预算" in service.recommend_reason(kept[0], profile, "hybrid")


@pytest.mark.parametrize(
    ("text", "expected_min", "expected_max"),
    [
        ("推荐一款1500元左右的手机", 1200.0, 1800.0),
        ("预算大约1500元的手机", 1200.0, 1800.0),
        ("1500元以内的手机", None, 1500.0),
    ],
)
def test_extract_profile_understands_common_budget_phrases(
    text: str,
    expected_min: float | None,
    expected_max: float,
):
    profile = extract_profile(text)

    assert profile["budgetMin"] == expected_min
    assert profile["budgetMax"] == expected_max


def test_recommend_reason_never_claims_an_over_budget_product_matches():
    service = ShoppingProfileService()
    profile = extract_profile("预算1500元以内的手机")
    product = {
        "product_id": "expensive",
        "product_name": "旗舰手机",
        "min_price": 6379,
        "max_price": 8999,
        "status": 1,
    }

    assert not service.matches_budget(product, profile)
    assert "符合预算" not in service.recommend_reason(product, profile, "hybrid")


def test_accept_substitute_turns_brand_into_soft_preference():
    service = ShoppingProfileService()
    profile = extract_profile("预算3000以内的华为手机，其他品牌也可以")
    products = [
        {"product_id": "1", "product_name": "荣耀手机", "min_price": 1999, "status": 1},
        {"product_id": "2", "product_name": "华为手机", "min_price": 2499, "status": 1},
    ]

    assert profile["acceptSubstitute"] is True
    assert [item["product_id"] for item in service.filter_products(products, profile)] == ["1", "2"]


def test_filter_products_rejects_known_out_of_stock_but_keeps_unknown_stock():
    service = ShoppingProfileService()
    profile = extract_profile("预算3000以内的手机")
    products = [
        {"product_id": "1", "product_name": "手机A", "min_price": 1999, "total_stock": 0, "status": 1},
        {"product_id": "2", "product_name": "手机B", "min_price": 2499, "in_stock": False, "status": 1},
        {"product_id": "3", "product_name": "手机C", "min_price": 2599, "status": 1},
        {"product_id": "4", "product_name": "手机D", "min_price": 2799, "total_stock": 5, "status": 1},
    ]

    kept = service.filter_products(products, profile)

    assert [item["product_id"] for item in kept] == ["3", "4"]


def test_constraint_summary_is_user_readable():
    profile = extract_profile("预算3000以内的华为手机，办公")

    assert ShoppingProfileService.summary(profile) == "预算不超过3000元、偏好华为、类别手机、场景办公"


# --------------------------------------------------------------------------- #
# Redis 缓存 + MySQL 事实源                                                    #
#                                                                             #
# Redis 有 TTL 也会重启，所以它不能是唯一存储：缓存一没，用户之前说过的预算和品牌      #
# 约束就凭空消失了，而用户不会知道要再说一遍。这一组测的就是"缓存失效不等于偏好丢失"。 #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_profile_falls_back_to_db_when_cache_misses(monkeypatch):
    stored = {**empty_profile(), "category": "手机", "budgetMax": 3000.0}
    _, cm = _db_cursor({"profile_json": json.dumps(stored, ensure_ascii=False)})
    service = ShoppingProfileService()

    saved: dict = {}

    async def fake_save(user_id, profile):
        saved[user_id] = profile

    monkeypatch.setattr(
        "app.services.shopping_profile_service.redis_service.get_shopping_profile",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(service, "_save_redis", fake_save)

    with patch("app.services.shopping_profile_service.acquire", return_value=cm):
        profile = await service.get_profile("u1")

    assert profile["category"] == "手机"
    assert profile["budgetMax"] == 3000.0
    # 命中 DB 之后要回填缓存，否则每一轮对话都要多打一次 MySQL。
    assert saved["u1"]["category"] == "手机"


@pytest.mark.asyncio
async def test_profile_falls_back_to_db_when_redis_raises(monkeypatch):
    """Redis 整个挂掉（不是未命中）时也要走 DB，不能直接返回空档案。"""
    stored = {**empty_profile(), "category": "耳机"}
    _, cm = _db_cursor({"profile_json": stored})  # 驱动也可能已经反序列化好
    service = ShoppingProfileService()

    monkeypatch.setattr(
        "app.services.shopping_profile_service.redis_service.get_shopping_profile",
        AsyncMock(side_effect=RuntimeError("redis down")),
    )
    monkeypatch.setattr(service, "_save_redis", AsyncMock())

    with patch("app.services.shopping_profile_service.acquire", return_value=cm):
        profile = await service.get_profile("u1")

    assert profile["category"] == "耳机"


@pytest.mark.asyncio
async def test_corrupt_db_json_degrades_to_empty_profile(monkeypatch):
    """DB 里存的 JSON 坏了要当作没有档案，而不是把异常抛到对话链路上。"""
    _, cm = _db_cursor({"profile_json": "{not json"})
    service = ShoppingProfileService()

    monkeypatch.setattr(
        "app.services.shopping_profile_service.redis_service.get_shopping_profile",
        AsyncMock(return_value=None),
    )

    with patch("app.services.shopping_profile_service.acquire", return_value=cm):
        profile = await service.get_profile("u1")

    assert profile == empty_profile()


@pytest.mark.asyncio
async def test_db_outage_does_not_break_an_ordinary_chat_turn(monkeypatch):
    """MySQL 挂了，取档案要返回空档案让对话继续，不能抛。"""
    service = ShoppingProfileService()
    monkeypatch.setattr(
        "app.services.shopping_profile_service.redis_service.get_shopping_profile",
        AsyncMock(return_value=None),
    )

    def boom(*args, **kwargs):
        raise RuntimeError("mysql down")

    with patch("app.services.shopping_profile_service.acquire", side_effect=boom):
        profile = await service.get_profile("u1")

    assert profile == empty_profile()


@pytest.mark.asyncio
async def test_update_writes_through_to_both_stores(monkeypatch):
    """更新要同时落缓存和 DB。只落 Redis 的话重启就丢，只落 DB 则每轮都要查库。"""
    service = ShoppingProfileService()
    calls: list[str] = []

    monkeypatch.setattr(service, "get_profile", AsyncMock(return_value=empty_profile()))

    async def note_redis(user_id, profile):
        calls.append("redis")

    async def note_db(user_id, profile):
        calls.append("db")

    monkeypatch.setattr(service, "_save_redis", note_redis)
    monkeypatch.setattr(service, "_save_db", note_db)

    merged = await service.update_profile("u1", "想买3000以内的华为手机")

    assert merged["budgetMax"] == 3000
    assert sorted(calls) == ["db", "redis"]


# --------------------------------------------------------------------------- #
# 后台 LLM 富化                                                                #
#                                                                             #
# 这条路径是 asyncio.create_task 起的，异常不会有人看——所以"失败必须静默且不影响       #
# 主链路"本身就是要被测的行为，而不是可以省略的边界情况。                             #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_short_text_does_not_spend_an_llm_call(monkeypatch):
    """短文本正则就能处理，不值得花一次 LLM 调用。"""
    service = ShoppingProfileService()
    called = False

    async def fake_extract(text):
        nonlocal called
        called = True
        return None

    monkeypatch.setattr(service, "_llm_extract_profile", fake_extract)

    await service.async_enrich_profile("u1", "买手机")

    assert not called


@pytest.mark.asyncio
async def test_llm_enrichment_never_persists_inferred_preferences(monkeypatch):
    """模型推断不等于用户明确表达，不能自动进入长期记忆。"""
    service = ShoppingProfileService()
    enriched = {**empty_profile(), "category": "笔记本电脑", "scenarios": ["办公"]}

    monkeypatch.setattr(service, "_llm_extract_profile", AsyncMock(return_value=enriched))
    save_redis = AsyncMock()
    save_db = AsyncMock()
    monkeypatch.setattr(service, "_save_redis", save_redis)
    monkeypatch.setattr(service, "_save_db", save_db)

    await service.async_enrich_profile("u1", "我想找一台适合日常办公用的机器，轻薄一点")

    service._llm_extract_profile.assert_not_awaited()
    save_redis.assert_not_awaited()
    save_db.assert_not_awaited()


@pytest.mark.asyncio
async def test_enrichment_failure_is_silent(monkeypatch):
    """LLM 抛异常不能让后台任务把异常冒到事件循环里。"""
    service = ShoppingProfileService()
    monkeypatch.setattr(
        service, "_llm_extract_profile", AsyncMock(side_effect=RuntimeError("llm down")),
    )
    monkeypatch.setattr(service, "_save_redis", AsyncMock())
    monkeypatch.setattr(service, "_save_db", AsyncMock())

    await service.async_enrich_profile("u1", "我想找一台适合日常办公用的机器，轻薄一点")


@pytest.mark.asyncio
async def test_signal_free_llm_result_is_not_persisted(monkeypatch):
    """LLM 什么都没提取到时不该写库——空档案覆盖会清掉用户已有的偏好。"""
    service = ShoppingProfileService()
    monkeypatch.setattr(
        service, "_llm_extract_profile", AsyncMock(return_value=empty_profile()),
    )
    written = False

    async def note(user_id, profile):
        nonlocal written
        written = True

    monkeypatch.setattr(service, "_save_redis", note)
    monkeypatch.setattr(service, "_save_db", note)

    await service.async_enrich_profile("u1", "今天天气不错，随便看看有什么新鲜玩意儿")

    assert not written


@pytest.mark.asyncio
async def test_llm_json_is_parsed_through_markdown_fences(monkeypatch):
    """模型爱把 JSON 包在 ```json 里，这是最常见的一种解析失败。"""
    service = ShoppingProfileService()
    payload = {
        "category": "手机",
        "budgetMin": None,
        "budgetMax": 3000,
        "brands": ["苹果"],
        "excludedBrands": [],
        "scenarios": ["学生"],
        "features": ["性价比"],
        "acceptSubstitute": True,
    }
    fenced = "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"

    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(content=fenced))
    monkeypatch.setattr(
        "app.services.llm_factory.create_memory_llm", MagicMock(return_value=llm)
    )

    result = await service._llm_extract_profile("我是学生，想买台三千以内的苹果手机")

    assert result["category"] == "手机"
    assert result["budgetMax"] == 3000.0
    assert result["brands"] == ["苹果"]
    assert result["acceptSubstitute"] is True


@pytest.mark.asyncio
async def test_llm_garbage_values_are_coerced_not_propagated(monkeypatch):
    """模型可能把预算写成 "三千"、把列表写成 null。这些都要被归一化掉。

    不做归一化的话，脏值会一路写进 Redis 和 MySQL，之后每一轮对话都带着它。
    """
    service = ShoppingProfileService()
    payload = {
        "category": "  手机  ",
        "budgetMin": "不限",
        "budgetMax": "三千",
        "brands": None,
        "excludedBrands": ["", "小米"],
        "scenarios": None,
        "features": [f"f{i}" for i in range(20)],
        "acceptSubstitute": "yes",
    }
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(content=json.dumps(payload, ensure_ascii=False)))
    monkeypatch.setattr(
        "app.services.llm_factory.create_memory_llm", MagicMock(return_value=llm)
    )

    result = await service._llm_extract_profile("随便说点什么凑够十五个字的长度")

    assert result["category"] == "手机"
    assert result["budgetMin"] is None
    assert result["budgetMax"] is None
    assert result["brands"] == []
    assert result["excludedBrands"] == ["小米"]
    assert len(result["features"]) == 10, "列表没有截断，脏数据会一直留在档案里"
    # "yes" 是真值但不是布尔。当成 True 会让"是否接受替代品牌"这种影响推荐结果的
    # 字段被字符串的真值性决定，所以只认真正的 bool。
    assert result["acceptSubstitute"] is None


@pytest.mark.asyncio
async def test_non_json_llm_response_returns_none(monkeypatch):
    service = ShoppingProfileService()
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(content="抱歉，我无法完成这个请求。"))
    monkeypatch.setattr(
        "app.services.llm_factory.create_memory_llm", MagicMock(return_value=llm)
    )

    assert await service._llm_extract_profile("随便说点什么凑够十五个字的长度") is None
