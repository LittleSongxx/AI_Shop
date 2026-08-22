from app.config.settings import Settings, get_settings
from app.services import agent_runtime
from app.services.agent_runtime import bind_agent_llm
from app.services.llm_factory import (
    _is_deepseek_endpoint,
    _is_qwen_compatible_endpoint,
    _resolve_memory_llm_config,
    chat_llm_config,
    create_chat_llm,
    create_memory_llm,
    has_fallback_chat_llm,
)


def test_memory_llm_falls_back_to_chat_config(monkeypatch):
    s = Settings(
        llm_api_key="chat-key",
        llm_base_url="https://chat.example",
        llm_model="chat-model",
        llm_timeout=60,
    )
    api_key, base_url, model, timeout = _resolve_memory_llm_config(s)
    assert api_key == "chat-key"
    assert base_url == "https://chat.example"
    assert model == "chat-model"
    assert timeout == 60


def test_memory_llm_uses_dedicated_config(monkeypatch):
    s = Settings(
        llm_api_key="chat-key",
        llm_base_url="https://chat.example",
        llm_model="chat-model",
        memory_llm_api_key="mem-key",
        memory_llm_base_url="https://mem.example",
        memory_llm_model="mem-model",
        memory_llm_timeout=90,
    )
    api_key, base_url, model, timeout = _resolve_memory_llm_config(s)
    assert api_key == "mem-key"
    assert base_url == "https://mem.example"
    assert model == "mem-model"
    assert timeout == 90


def test_chat_and_memory_llm_streaming_flag(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    get_settings.cache_clear()
    chat = create_chat_llm()
    memory = create_memory_llm()
    assert chat.streaming is True
    assert memory.streaming is False
    get_settings.cache_clear()


def test_bounded_deepseek_memory_work_can_disable_thinking(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("LLM_MODEL", "deepseek-v4-flash")
    get_settings.cache_clear()
    try:
        regular = create_memory_llm()
        extraction = create_memory_llm(disable_thinking=True)

        assert regular.extra_body is None
        assert extraction.extra_body == {"thinking": {"type": "disabled"}}
        assert extraction.streaming is False
    finally:
        get_settings.cache_clear()


def test_bounded_deepseek_agent_work_can_disable_thinking(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("LLM_MODEL", "deepseek-v4-flash")
    get_settings.cache_clear()
    try:
        regular = chat_llm_config()
        bounded = chat_llm_config(disable_thinking=True)

        assert regular.disable_thinking is False
        assert bounded.disable_thinking is True
        assert bind_agent_llm(disable_thinking=True) is not bind_agent_llm()
    finally:
        agent_runtime._agent_llm_with_tools.cache_clear()
        get_settings.cache_clear()


def test_thinking_toggle_is_only_sent_to_deepseek_compatible_endpoints():
    assert _is_deepseek_endpoint("https://api.deepseek.com") is True
    assert _is_deepseek_endpoint("https://deepseek.com/v1") is True
    assert _is_deepseek_endpoint("https://api.openai.com/v1") is False


def test_bounded_qwen_work_uses_provider_specific_thinking_toggle(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv(
        "LLM_BASE_URL",
        "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    )
    monkeypatch.setenv("LLM_MODEL", "qwen3.7-flash")
    get_settings.cache_clear()
    try:
        regular = chat_llm_config()
        bounded = chat_llm_config(disable_thinking=True, streaming=False)

        assert _is_qwen_compatible_endpoint(bounded.base_url) is True
        assert regular.disable_thinking is False
        assert bounded.disable_thinking is True
        assert create_memory_llm(disable_thinking=True).extra_body == {
            "enable_thinking": False
        }
    finally:
        get_settings.cache_clear()


def test_chat_llm_can_switch_to_distinct_fallback_model(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "primary-model")
    monkeypatch.setenv("LLM_FALLBACK_MODEL", "fallback-model")
    get_settings.cache_clear()
    try:
        primary = create_chat_llm()
        fallback = create_chat_llm(fallback=True)
        assert primary.model_name == "primary-model"
        assert fallback.model_name == "fallback-model"
        assert has_fallback_chat_llm() is True
    finally:
        get_settings.cache_clear()


def test_same_model_does_not_enable_fallback(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "same-model")
    monkeypatch.setenv("LLM_FALLBACK_MODEL", "same-model")
    get_settings.cache_clear()
    try:
        assert has_fallback_chat_llm() is False
    finally:
        get_settings.cache_clear()


def test_chat_llm_is_reused_but_rebuilt_when_config_changes(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "model-a")
    get_settings.cache_clear()
    try:
        # Reusing the instance is the point: it owns the httpx connection pool.
        assert create_chat_llm() is create_chat_llm()

        monkeypatch.setenv("LLM_MODEL", "model-b")
        get_settings.cache_clear()
        assert create_chat_llm().model_name == "model-b"
    finally:
        get_settings.cache_clear()


def test_agent_llm_reuses_one_tool_binding_per_config(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "primary-model")
    monkeypatch.setenv("LLM_FALLBACK_MODEL", "fallback-model")
    get_settings.cache_clear()
    try:
        # Every ReAct round calls this; the schema conversion must not repeat.
        assert bind_agent_llm() is bind_agent_llm()
        assert bind_agent_llm(fallback=True) is not bind_agent_llm()
    finally:
        get_settings.cache_clear()


def test_agent_llm_distinguishes_empty_scope_from_legacy_full_scope(monkeypatch):
    config = object()
    captured_scopes = []

    class FakeLLM:
        def bind_tools(self, tools):
            return ("bound", tools)

    monkeypatch.setattr(agent_runtime, "chat_llm_config", lambda **_kwargs: config)
    monkeypatch.setattr(agent_runtime, "chat_llm_for_config", lambda _config: FakeLLM())
    monkeypatch.setattr(
        agent_runtime,
        "build_mcp_tools",
        lambda allowed: (
            captured_scopes.append(allowed) or (["legacy"] if allowed is None else list(allowed))
        ),
    )
    agent_runtime._agent_llm_with_tools.cache_clear()
    try:
        assert bind_agent_llm(allowed_tools=frozenset()) == ("bound", [])
        assert bind_agent_llm() == ("bound", ["legacy"])
        assert captured_scopes == [set(), None]
    finally:
        agent_runtime._agent_llm_with_tools.cache_clear()


def test_agent_llm_can_skip_tool_binding_for_final_artifact(monkeypatch):
    config = object()
    llm = object()
    build_tools = []

    monkeypatch.setattr(agent_runtime, "chat_llm_config", lambda **_kwargs: config)
    monkeypatch.setattr(agent_runtime, "chat_llm_for_config", lambda _config: llm)
    monkeypatch.setattr(
        agent_runtime,
        "build_mcp_tools",
        lambda allowed: build_tools.append(allowed) or [],
    )

    assert bind_agent_llm(tools_enabled=False) is llm
    assert build_tools == []
