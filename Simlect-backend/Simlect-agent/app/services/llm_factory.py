from langchain_openai import ChatOpenAI

from app.config.settings import Settings, get_settings


def create_chat_llm(*, fallback: bool = False) -> ChatOpenAI:

    s = get_settings()
    _require_api_key(s.llm_api_key, "LLM_API_KEY")
    model = s.llm_fallback_model if fallback else s.llm_model
    return ChatOpenAI(
        api_key=s.llm_api_key,
        base_url=s.llm_base_url,
        model=model,
        timeout=s.llm_timeout,
        max_retries=s.llm_max_retries,
        streaming=True,
    )


def has_fallback_chat_llm() -> bool:
    s = get_settings()
    return bool(
        s.llm_fallback_model.strip()
        and s.llm_fallback_model.strip() != s.llm_model.strip()
    )

def create_memory_llm() -> ChatOpenAI:

    s = get_settings()
    api_key, base_url, model, timeout = _resolve_memory_llm_config(s)
    _require_api_key(api_key, "MEMORY_LLM_API_KEY or LLM_API_KEY")
    return ChatOpenAI(
        api_key=api_key,
        base_url=base_url,
        model=model,
        timeout=timeout,
        streaming=False,
    )

def _resolve_memory_llm_config(s: Settings) -> tuple[str, str, str, int]:

    api_key = (s.memory_llm_api_key or s.llm_api_key).strip()
    base_url = (s.memory_llm_base_url or s.llm_base_url).strip()
    model = (s.memory_llm_model or s.llm_model).strip()

    timeout = s.memory_llm_timeout if s.memory_llm_timeout is not None else s.llm_timeout
    return api_key, base_url, model, timeout

def create_llm() -> ChatOpenAI:

    return create_chat_llm()


def _require_api_key(value: str, setting_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{setting_name} is required before creating an LLM client")
