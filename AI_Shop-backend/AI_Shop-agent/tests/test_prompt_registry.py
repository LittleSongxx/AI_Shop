"""D 工作线：prompt 片段注册表 + selectedFragments 可观测性。

断言三件事：注册表与 prompts/ 落盘文件双向一致（单一事实源）；
内联的 ReAct 补充说明已收敛为 corpus 片段；build_agent_system_prompt
每次组装都把实际选用的片段（含 Redis 覆盖来源）写进 selection_out。
"""

from pathlib import Path

import pytest

from app.domain.intent.types import IntentKind
from app.services import prompt_service
from app.services.prompt_service import (
    PROMPT_DIR,
    PROMPT_FILE_MAP,
    PROMPT_FRAGMENTS,
    build_agent_system_prompt,
    load_prompt,
    load_prompt_with_source,
)

REACT_MARKER = "=== ReAct 执行说明"


class _StubRedis:
    class _Client:
        async def get(self, _key: str):
            return None

    client = _Client()


@pytest.fixture(autouse=True)
def _stub_redis(monkeypatch):
    monkeypatch.setattr(prompt_service, "redis_service", _StubRedis())


def test_registry_matches_corpus_both_directions():
    # 每个注册片段都有落盘文件；prompts/ 里每个文件都有注册条目。
    for fragment in PROMPT_FRAGMENTS:
        assert (PROMPT_DIR / fragment.filename).exists(), fragment.filename
        assert PROMPT_FILE_MAP[fragment.prompt_key] == fragment.filename
    registered_files = {f.filename for f in PROMPT_FRAGMENTS}
    on_disk = {p.name for p in Path(PROMPT_DIR).glob("*.txt")}
    assert registered_files == on_disk


@pytest.mark.asyncio
async def test_react_supplement_is_corpus_fragment():
    # 原内联常量已收敛为 corpus 片段：可经统一加载路径读取、可被 Redis 覆盖。
    text, source = await load_prompt_with_source("react_supplement")
    assert source == "file"
    assert REACT_MARKER in text
    assert "PROPOSE_*" in text


@pytest.mark.asyncio
async def test_load_prompt_with_source_reports_redis_override(monkeypatch):
    class _OverrideClient:
        async def get(self, key: str):
            return f"OVERRIDDEN:{key}"

    class _OverrideRedis:
        client = _OverrideClient()

    monkeypatch.setattr(prompt_service, "redis_service", _OverrideRedis())

    text, source = await load_prompt_with_source("global")
    assert text.startswith("OVERRIDDEN:")
    assert source == "redis"
    # load_prompt 薄包装保持返回文本。
    assert (await load_prompt("global")).startswith("OVERRIDDEN:")


@pytest.mark.asyncio
async def test_build_records_core_intent_react_selection():
    selection: list[dict] = []
    text = await build_agent_system_prompt(
        IntentKind.QUERY_ORDER,
        "u1",
        "查一下我的订单",
        selection_out=selection,
    )
    fragments = [f["fragment"] for f in selection]
    assert fragments == ["global", "intent", "react_supplement"]
    assert selection[0]["promptKey"] == "global"
    assert selection[0]["source"] == "file"
    assert selection[1]["promptKey"] == "query_order"
    assert selection[2]["promptKey"] == "react_supplement"
    assert all(f["chars"] > 0 for f in selection)
    assert REACT_MARKER in text


@pytest.mark.asyncio
async def test_build_records_knowledge_inline_when_injected():
    selection: list[dict] = []
    await build_agent_system_prompt(
        IntentKind.REFUND,
        "u1",
        "怎么退货",
        knowledge_text="退货需保持包装完整。",
        selection_out=selection,
    )
    fragments = [f["fragment"] for f in selection]
    assert "knowledge_inline" in fragments
    assert selection[fragments.index("knowledge_inline")]["source"] == "runtime"


@pytest.mark.asyncio
async def test_build_knowledge_skipped_for_chat_intent():
    selection: list[dict] = []
    await build_agent_system_prompt(
        IntentKind.CHAT,
        "u1",
        "你好",
        knowledge_text="退货需保持包装完整。",
        selection_out=selection,
    )
    fragments = [f["fragment"] for f in selection]
    assert "knowledge_inline" not in fragments


@pytest.mark.asyncio
async def test_build_global_missing_falls_back_to_agent(monkeypatch):
    async def fake_load(key: str) -> tuple[str, str]:
        if key == "global":
            return "", "file"
        return {"agent": ("AGENT_TEMPLATE", "file")}.get(key, ("", "file"))

    monkeypatch.setattr(
        "app.services.prompt_service.load_prompt_with_source", fake_load
    )
    selection: list[dict] = []
    text = await build_agent_system_prompt(
        IntentKind.CHAT, "u1", "你好", selection_out=selection
    )
    assert text.startswith("AGENT_TEMPLATE")
    assert selection[0]["fragment"] == "agent_fallback"
    assert selection[0]["promptKey"] == "agent"


@pytest.mark.asyncio
async def test_build_without_selection_is_plain_string():
    text = await build_agent_system_prompt(IntentKind.CHAT, "u1", "你好")
    assert isinstance(text, str)
    assert "当前意图" in text
