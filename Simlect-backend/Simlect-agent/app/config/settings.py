from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):

    model_config = SettingsConfigDict(

        env_file=".env",

        env_file_encoding="utf-8",

        extra="ignore",
        populate_by_name=True,
    )

    app_host: str = "0.0.0.0"
    app_port: int = 7050
    app_env: str = "development"
    allow_development_auth_bypass: bool = False
    java_web_url: str = "http://localhost:8080"
    mcp_server_url: str = Field(
        default="http://127.0.0.1:7060",
        validation_alias=AliasChoices("MCP_SERVER_URL", "mcp_server_url"),
    )
    mcp_timeout: int = 20

    internal_token: str = Field(
        default="your-token",
        validation_alias=AliasChoices(
            "SIMLECT_INTERNAL_TOKEN",
            "INTERNAL_TOKEN",
            "internal_token",
        ),
    )

    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"
    llm_fallback_model: str = "deepseek-chat"
    llm_timeout: int = 60
    llm_max_retries: int = 3

    memory_llm_api_key: str = ""
    memory_llm_base_url: str = ""
    memory_llm_model: str = ""

    memory_llm_timeout: int | None = None

    embedding_api_key: str = ""
    embedding_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    embedding_model: str = "text-embedding-v4"
    embedding_dimensions: int = 1024
    embedding_cache_ttl_seconds: int = 7 * 24 * 60 * 60

    rerank_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("DASHSCOPE_API_KEY", "RERANK_API_KEY", "rerank_api_key"),
    )
    rerank_base_url: str = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
    rerank_model: str = "gte-rerank-v2"
    rerank_timeout: int = 20
    rerank_top_n: int = 6

    mysql_host: str = "localhost"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_password: str = "123456"
    mysql_database: str = "simlect_agent"

    redis_host: str = "127.0.0.1"
    redis_port: int = 6379
    redis_db: int = 0

    es_hosts: str = "http://localhost:9200"
    es_index: str = "simlect_vectorstore"
    es_vector_dimensions: int = 1024

    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/"
    agent_queue_exchange: str = "agent.tasks"
    agent_worker_high_concurrency: int = 4
    agent_worker_fast_concurrency: int = 4
    agent_worker_low_concurrency: int = 2
    agent_task_max_retries: int = 5
    agent_task_deadline_seconds: int = 120
    agent_task_recovery_interval_seconds: int = 5
    agent_user_lock_ttl_seconds: int = 180
    agent_support_summary_limit: int = 12
    agent_worker_heartbeat_ttl_seconds: int = 30
    support_first_response_sla_seconds: int = 300
    support_queue_alert_seconds: int = 600

    ai_chat_limit: int = 200
    rag_top_k: int = 15
    rag_score_threshold: float = 0.5
    rag_cache_ttl_seconds: int = 30 * 60
    faq_exact_cache_ttl_seconds: int = 6 * 60 * 60
    faq_fast_path_timeout_seconds: float = 1.5
    history_message_limit: int = 15
    task_queue_max: int = 300

    session_redis_ttl: int = 86400
    session_compress_lock_ttl: int = 60

    working_token_budget: int = 100_000
    compress_token_threshold: int = 100_000
    assistant_history_max_len: int = 500

    circuit_llm_failure_threshold: int = 5
    circuit_llm_recovery_timeout: int = 60

    graph_max_react_rounds: int = 5
    graph_checkpoint_ttl: int = 3600
    graph_checkpoint_prefix: str = "mall:agent:graph:ckpt"

    intent_use_llm: bool = True
    intent_rule_fallback: bool = True
    intent_handoff_confidence: float = 0.55
    order_query_lookback_days: int = 90
    force_mcp_on_llm_skip: bool = False

    def validate_runtime(self) -> None:
        if self.app_env.lower() != "production":
            return

        errors: list[str] = []
        if not self.internal_token.strip() or self.internal_token == "your-token":
            errors.append("SIMLECT_INTERNAL_TOKEN must be configured")
        if not self.llm_api_key.strip():
            errors.append("LLM_API_KEY must be configured")
        if not self.embedding_api_key.strip():
            errors.append("EMBEDDING_API_KEY must be configured")
        if self.allow_development_auth_bypass:
            errors.append("ALLOW_DEVELOPMENT_AUTH_BYPASS must be false")
        if errors:
            raise ValueError("Invalid production configuration: " + "; ".join(errors))

    @property
    def mysql_dsn(self) -> str:

        return (
            f"mysql+aiomysql://{self.mysql_user}:{self.mysql_password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}"
        )

    @property
    def redis_url(self) -> str:

        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

@lru_cache
def get_settings() -> Settings:

    return Settings()
