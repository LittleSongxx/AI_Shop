from langchain_openai import ChatOpenAI

from app.config.settings import Settings, get_settings

def create_chat_llm() -> ChatOpenAI:

    s = get_settings()
    return ChatOpenAI(
        api_key=s.llm_api_key,
        base_url=s.llm_base_url,
        model=s.llm_model,
        timeout=s.llm_timeout,
        streaming=True,
    )

def create_memory_llm() -> ChatOpenAI:

    s = get_settings()
    api_key, base_url, model, timeout = _resolve_memory_llm_config(s)
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
