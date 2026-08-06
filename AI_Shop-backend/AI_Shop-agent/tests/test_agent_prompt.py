from pathlib import Path

PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "agent.txt"

def _load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")

def test_prompt_has_intent_routing_section():
    text = _load_prompt()
    assert "意图与工具选择" in text
    assert "系统会先按商品、状态和时间解析本人订单" in text
    assert "不要空参调用写工具" in text

def test_prompt_few_shot_review_examples():
    text = _load_prompt()
    assert "Few-shot 示例" in text
    assert "写评价-信息不足" in text
    assert "PROPOSE_PRODUCT_REVIEW" in text
    assert "QUERY_COMMENT" in text
    assert "不调用** QUERY_ORDERS" in text or "不调用 QUERY_ORDERS" in text

def test_prompt_distinguish_read_write_tools():
    text = _load_prompt()
    assert "QUERY_ORDERS：**仅**查询订单" in text
    assert "QUERY_COMMENT：**仅**查看已提交的评价" in text
    assert "PROPOSE_PRODUCT_REVIEW：**提交**评价提案" in text

def test_prompt_has_forbidden_section():
    text = _load_prompt()
    assert "=== 【禁止】===" in text
    assert text.count("【禁止】") >= 15
    assert "【禁止】句中出现「订单」二字就默认调用 QUERY_ORDERS" in text
    assert "【禁止】把模型猜测出的订单号或订单项传给 PROPOSE_*" in text
    assert "退款、确认收货、追评等写意图先 QUERY_ORDERS" not in text

def test_prompt_write_ops_no_query_orders_fallback():
    text = _load_prompt()
    for phrase in ("确认收货", "退款", "追评"):
        assert phrase in text

def test_system_prompt_static_prefix_stable_across_calls(monkeypatch):
    """B3 静态前置契约：跨意图、跨用户文本，system prompt 的静态前缀必须字节级一致。

    prefix cache 的命中原则就是"前缀字节级稳定"——任何动态内容混进前缀
    （时间戳/用户ID/请求ID）都会让每次调用都 miss。这里把契约钉死：
    两个不同的意图与文本构造出的 prompt，在首个动态标记
    （=== 当前意图）之前的部分必须完全相等。
    """
    import asyncio

    from app.domain.intent.types import IntentKind
    from app.services import prompt_service
    from app.services.prompt_service import build_agent_system_prompt

    class _StubRedis:
        class _Client:
            async def get(self, _key: str):
                return None

        client = _Client()

    monkeypatch.setattr(prompt_service, "redis_service", _StubRedis())

    async def build(intent: IntentKind, user_text: str) -> str:
        return await build_agent_system_prompt(
            intent,
            "user-001",
            user_text,
            product_snapshot=None,
            faq_text=None,
            knowledge_text=None,
        )

    a = asyncio.run(build(IntentKind.QUERY_ORDER, "查一下我的订单"))
    b = asyncio.run(build(IntentKind.PRODUCT_SEARCH, "帮我搜耳机"))
    marker = "=== 当前意图："
    prefix_a = a.split(marker)[0] if marker in a else a
    prefix_b = b.split(marker)[0] if marker in b else b
    assert prefix_a == prefix_b, "system prompt 静态前缀不稳定，prefix cache 会失效"
    assert marker in a and marker in b
    assert "user-001" not in prefix_a
    assert "查一下我的订单" not in prefix_a and "帮我搜耳机" not in prefix_a
