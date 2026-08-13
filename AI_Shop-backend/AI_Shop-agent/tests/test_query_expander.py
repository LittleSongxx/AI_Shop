import pytest

from app.config.settings import get_settings
from app.rag.query_expander import deterministic_query_variants, expand_query


def test_deterministic_variants_keep_original_first_and_add_business_aliases():
    variants = deterministic_query_variants("退款进度怎么查")

    assert variants[0] == "退款进度怎么查"
    assert len(variants) == 2
    assert "退钱" in variants[1]


@pytest.mark.asyncio
async def test_unconfigured_llm_uses_only_deterministic_variants(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "")
    get_settings.cache_clear()
    try:
        variants = await expand_query("默认地址怎么改")
    finally:
        get_settings.cache_clear()

    assert variants[0] == "默认地址怎么改"
    assert len(variants) <= 3
