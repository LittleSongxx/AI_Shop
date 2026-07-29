from functools import lru_cache
from typing import Annotated

from pydantic import AliasChoices, BeforeValidator, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _blank_to_none(value: object) -> object:
    """把 `.env` 里的空值读成"未配置"。

    dotenv 表达"这一项没配"的方式是留空（`MEMORY_LLM_TIMEOUT=`），不是删掉整行——
    删掉就看不出还有这个可选项了。但 pydantic 无法把 `""` 强转成 int，于是
    `cp .env.example .env` 之后 Settings() 直接抛 ValidationError，应用、评测 runner、
    以及所有 import 了模块级单例的测试全部起不来。

    只给可选数值字段用。字符串字段本来就接受空值，不需要绕这一圈。
    """
    if isinstance(value, str) and not value.strip():
        return None
    return value


OptionalInt = Annotated[int | None, BeforeValidator(_blank_to_none)]


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
    # Autoreload respawns the server on file changes and is a local-dev tool
    # only; leaving it on elsewhere costs a watcher process and restarts.
    app_reload: bool = False
    allow_development_auth_bypass: bool = False
    otel_enabled: bool = False
    otel_service_name: str = "aishop-agent"
    otel_otlp_endpoint: str = ""
    java_web_url: str = "http://localhost:8080"
    mcp_server_url: str = Field(
        default="http://127.0.0.1:7060",
        validation_alias=AliasChoices("MCP_SERVER_URL", "mcp_server_url"),
    )
    mcp_timeout: int = 20

    internal_token: str = Field(
        default="your-token",
        validation_alias=AliasChoices(
            "AISHOP_INTERNAL_TOKEN",
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

    # 留空 = 回落 llm_timeout，见 _blank_to_none。
    memory_llm_timeout: OptionalInt = None

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
    mysql_database: str = "aishop_agent"
    agent_auto_migrate: bool = True

    redis_host: str = "127.0.0.1"
    # 6380 而不是 6379：compose 把宿主机端口偏移了一位，避开本机已装的 Redis。
    # 默认值必须指向项目的容器，否则会静默连上本机那个 Redis（连得通但是错的库）。
    redis_port: int = 6380
    redis_db: int = 0

    es_hosts: str = "http://localhost:9200"
    es_index: str = "aishop_vectorstore"
    es_vector_field: str = Field(
        default="embedding",
        validation_alias=AliasChoices("VECTOR_FIELD", "ES_VECTOR_FIELD", "es_vector_field"),
    )
    es_vector_dimensions: int = 1024

    # 账号和端口都跟 compose 对齐：aishop/aishop + 5673。
    rabbitmq_url: str = "amqp://aishop:aishop@localhost:5673/"
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

    # ---- 检索阈值：三个阶段的分数量纲互不相同，不能共用一个常量 ----
    #
    # 原先只有一个 rag_score_threshold=0.5，被同时用在下面三处。这不是"取值调得
    # 不够好"，而是 0.5 在三处的含义根本不同：
    #   _vector_search      ES cosine 打分是 (1+cos)/2，0.5 ⇔ cos>=0，只排除负相关；
    #   商品向量召回        原先在 retriever.py 里写死 0.4 ⇔ cos>=-0.2，比中性点还低，等于不过滤；
    #   _has_enough_evidence 拿到的是 BM25 原始分（1~20，恒过）或 rerank 归一分（0~1，合理）。
    # 所以那道"证据是否充分"的闸门实际几乎恒为真，而两道向量阈值几乎不筛东西。
    #
    # 现在按语义拆开，并且向量阈值直接用 cosine 表达——(1+cos)/2 这层换算属于 ES 的
    # 实现细节，配置项不该让人心算。换算在 retriever.cosine_to_es_score() 里。
    #
    # 取值依据与验证方法：DashScope text-embedding-v4 在中文短查询上，相关文档的
    # cosine 多在 0.4~0.8，无关文档在 0.1~0.3。0.30 取在两个分布之间偏保守的位置——
    # 宁可放进来交给 rerank 筛，也不要在召回阶段就把边缘相关的丢掉（召回阶段的漏召
    # 无法在后续任何阶段补回）。这两个数需要用 benchmarks/search_relevance_v1.jsonl
    # 在真实 ES 上标定，当前取值是保守默认而不是实测最优。
    rag_vector_min_cosine: float = 0.30
    # 商品召回比知识库召回更容忍噪声：搜出来的商品会再过一遍 rerank 和 MMR，
    # 而且用户能直接看出哪个不相关；知识库召回的噪声会被写进 prompt 当证据。
    rag_product_vector_min_cosine: float = 0.20
    # rerank 之后的归一化相关性（0~1）。这一道才是原来 0.5 唯一说得通的地方。
    rag_evidence_min_relevance: float = 0.5
    # 无 rerank 时的兜底闸门，单位是 RRF 融合分而不是任何引擎的原始分。
    # RRF 的全部意义就是丢掉不可比的原始分只留排名，所以这道闸门只能用排名表达：
    # "至少在某一路里进了前 N 名"。1/(60+N) 是 RRF 的定义式，N 越大越宽松。
    rag_evidence_min_rrf_rank: int = 10
    # ES requires num_candidates >= k. Raising it trades latency for recall, so
    # keep a floor rather than deriving it from k alone: at k=15 a bare 2x gives
    # the HNSW search very little room to escape a local minimum.
    knn_num_candidates_factor: int = 3
    knn_num_candidates_min: int = 100
    rag_cache_ttl_seconds: int = 30 * 60
    faq_exact_cache_ttl_seconds: int = 6 * 60 * 60
    faq_fast_path_timeout_seconds: float = 1.5
    history_message_limit: int = 15
    task_queue_max: int = 300

    session_redis_ttl: int = 86400
    session_compress_lock_ttl: int = 60

    working_token_budget: int = 16_000
    compress_token_threshold: int = 12_000
    assistant_history_max_len: int = 500
    max_input_chars: int = 4000

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

    @model_validator(mode="after")
    def validate_internal_contracts(self) -> "Settings":
        if self.embedding_dimensions != self.es_vector_dimensions:
            raise ValueError(
                "EMBEDDING_DIMENSIONS and ES_VECTOR_DIMENSIONS must be identical"
            )
        if self.es_vector_dimensions != 1024:
            raise ValueError("AI_Shop vector contract requires 1024 dimensions")
        if not self.es_vector_field.strip():
            raise ValueError("VECTOR_FIELD must not be empty")
        if self.compress_token_threshold >= self.working_token_budget:
            raise ValueError(
                "COMPRESS_TOKEN_THRESHOLD must be less than WORKING_TOKEN_BUDGET"
            )
        if self.max_input_chars < 128 or self.max_input_chars > 32_000:
            raise ValueError("MAX_INPUT_CHARS must be between 128 and 32000")
        return self

    def validate_runtime(self) -> None:
        if self.app_env.lower() != "production":
            return

        errors: list[str] = []
        if not self.internal_token.strip() or self.internal_token == "your-token":
            errors.append("AISHOP_INTERNAL_TOKEN must be configured")
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
