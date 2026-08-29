"""ChatOpenAI construction, cached by resolved configuration.

A ``ChatOpenAI`` owns an ``AsyncOpenAI`` client and therefore an httpx connection
pool, so building one per call throws away the pool and re-handshakes on every
request. The clients are stateless with respect to a conversation, so they are
cached and shared instead. Caching on the resolved config values (rather than on
``fallback`` alone) means a settings change produces a new key and a new client,
which is what keeps the tests honest after ``get_settings.cache_clear()``.

Like the db pool and redis client, a cached client assumes one event loop per
process, which is how the API and the worker both run.
"""

from functools import lru_cache
from typing import NamedTuple
from urllib.parse import urlparse

from langchain_openai import ChatOpenAI

from app.config.settings import Settings, get_settings


class ChatLLMConfig(NamedTuple):
    api_key: str
    base_url: str
    model: str
    timeout: int
    max_retries: int
    streaming: bool
    disable_thinking: bool


def chat_llm_config(
    *,
    fallback: bool = False,
    disable_thinking: bool = False,
    streaming: bool = True,
    max_retries: int | None = None,
) -> ChatLLMConfig:

    s = get_settings()
    if fallback:
        api_key = (getattr(s, "llm_fallback_api_key", "") or s.llm_api_key).strip()
        base_url = (getattr(s, "llm_fallback_base_url", "") or s.llm_base_url).strip()
        model = s.llm_fallback_model.strip()
        timeout = (
            getattr(s, "llm_fallback_timeout", None)
            if getattr(s, "llm_fallback_timeout", None) is not None
            else s.llm_timeout
        )
        configured_retries = (
            getattr(s, "llm_fallback_max_retries", None)
            if getattr(s, "llm_fallback_max_retries", None) is not None
            else s.llm_max_retries
        )
        _require_api_key(api_key, "LLM_FALLBACK_API_KEY or LLM_API_KEY")
    else:
        api_key = s.llm_api_key.strip()
        base_url = s.llm_base_url.strip()
        model = s.llm_model.strip()
        timeout = s.llm_timeout
        configured_retries = s.llm_max_retries
        _require_api_key(api_key, "LLM_API_KEY")
    return ChatLLMConfig(
        api_key=api_key,
        base_url=base_url,
        model=model,
        timeout=timeout,
        max_retries=(
            configured_retries
            if max_retries is None
            else max(0, int(max_retries))
        ),
        streaming=streaming,
        disable_thinking=disable_thinking
        and (_is_deepseek_endpoint(base_url) or _is_qwen_compatible_endpoint(base_url)),
    )


@lru_cache(maxsize=8)
def chat_llm_for_config(config: ChatLLMConfig) -> ChatOpenAI:

    return ChatOpenAI(
        api_key=config.api_key,
        base_url=config.base_url,
        model=config.model,
        timeout=config.timeout,
        max_retries=config.max_retries,
        streaming=config.streaming,
        extra_body=_thinking_extra_body(config.base_url) if config.disable_thinking else None,
    )


def create_chat_llm(*, fallback: bool = False) -> ChatOpenAI:

    return chat_llm_for_config(chat_llm_config(fallback=fallback))


def has_fallback_chat_llm() -> bool:
    s = get_settings()
    primary = (
        s.llm_api_key.strip(),
        s.llm_base_url.strip(),
        s.llm_model.strip(),
    )
    fallback = (
        (getattr(s, "llm_fallback_api_key", "") or s.llm_api_key).strip(),
        (getattr(s, "llm_fallback_base_url", "") or s.llm_base_url).strip(),
        s.llm_fallback_model.strip(),
    )
    return bool(fallback[2] and fallback != primary)


def create_memory_llm(*, disable_thinking: bool = False) -> ChatOpenAI:

    s = get_settings()
    api_key, base_url, model, timeout = _resolve_memory_llm_config(s)
    _require_api_key(api_key, "MEMORY_LLM_API_KEY or LLM_API_KEY")
    return chat_llm_for_config(
        ChatLLMConfig(
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout=timeout,
            max_retries=s.llm_max_retries,
            streaming=False,
            disable_thinking=disable_thinking
            and (_is_deepseek_endpoint(base_url) or _is_qwen_compatible_endpoint(base_url)),
        )
    )


def _resolve_memory_llm_config(s: Settings) -> tuple[str, str, str, int]:

    api_key = (s.memory_llm_api_key or s.llm_api_key).strip()
    base_url = (s.memory_llm_base_url or s.llm_base_url).strip()
    model = (s.memory_llm_model or s.llm_model).strip()

    timeout = s.memory_llm_timeout if s.memory_llm_timeout is not None else s.llm_timeout
    return api_key, base_url, model, timeout


def _is_deepseek_endpoint(base_url: str) -> bool:
    host = (urlparse(base_url).hostname or "").lower()
    return host == "deepseek.com" or host.endswith(".deepseek.com")


def _is_qwen_compatible_endpoint(base_url: str) -> bool:
    """Return whether the OpenAI-compatible endpoint accepts enable_thinking."""

    host = (urlparse(base_url).hostname or "").lower()
    return host == "aliyuncs.com" or host.endswith(".aliyuncs.com")


def _thinking_extra_body(base_url: str) -> dict | None:
    if _is_deepseek_endpoint(base_url):
        return {"thinking": {"type": "disabled"}}
    if _is_qwen_compatible_endpoint(base_url):
        return {"enable_thinking": False}
    return None


def create_llm() -> ChatOpenAI:

    return create_chat_llm()


def _require_api_key(value: str, setting_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{setting_name} is required before creating an LLM client")
