from functools import lru_cache
from typing import Annotated, Literal
from urllib.parse import urlparse

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
        env_ignore_empty=True,
        extra="ignore",
        populate_by_name=True,
    )

    app_host: str = "0.0.0.0"
    app_port: int = 7050
    app_version: str = "dev"
    # Worker 是独立进程，任务指标在 Worker 内更新；Prometheus 抓这个端口
    # （deploy/prometheus/prometheus.yml 已加 aishop-agent-worker job）。
    worker_metrics_port: int = 7051
    app_env: str = "development"
    # Autoreload respawns the server on file changes and is a local-dev tool
    # only; leaving it on elsewhere costs a watcher process and restarts.
    app_reload: bool = False
    otel_enabled: bool = False
    otel_service_name: str = "aishop-agent"
    otel_otlp_endpoint: str = ""
    tempo_query_url: str = "http://localhost:3000/explore"

    # Application-level Episode traces are independent of the OTLP exporter.
    # They stay useful in local/test environments where distributed tracing is off.
    episode_enabled: bool = True
    episode_queue_size: int = 2_000
    episode_batch_size: int = 100
    episode_flush_interval_ms: int = 200
    episode_success_sample_rate: float = 0.10
    episode_retention_days: int = 30

    # Shadow answer judge. An explicit model and usable API key are both required;
    # otherwise the queue stays disabled and the user path does no extra work.
    judge_model: str = ""
    judge_api_key: str = ""
    judge_base_url: str = ""
    judge_timeout: int = 15
    judge_sample_rate: float = 0.05
    judge_low_score_threshold: float = 0.65
    judge_queue_size: int = 500
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
    admin_assertion_current_secret: str = Field(
        default="your-token",
        validation_alias=AliasChoices(
            "AISHOP_ADMIN_ASSERTION_CURRENT_SECRET",
            "ADMIN_ASSERTION_CURRENT_SECRET",
            "admin_assertion_current_secret",
        ),
    )
    admin_assertion_current_key_id: str = "current"
    admin_assertion_previous_secret: str = ""
    admin_assertion_previous_key_id: str = "previous"
    admin_assertion_max_age_seconds: int = 300
    admin_assertion_nonce_ttl_seconds: int = 600
    # Stable HMAC identity used only to match an explicitly consented pilot
    # participant to an Agent run. It must not be rotated with the request
    # assertion key or raw user IDs would become impossible to match.
    pilot_identity_hmac_secret: str = "development-only-pilot-secret"
    privacy_export_signing_secret: str = "development-only-privacy-secret"
    privacy_export_dir: str = ".privacy-exports"
    privacy_export_ttl_seconds: int = 24 * 60 * 60

    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"
    llm_fallback_model: str = "deepseek-chat"
    llm_timeout: int = 60
    llm_max_retries: int = 3
    # 每百万 token 人民币单价。模型未配置时只累计 token 和 unpriced，
    # 不使用可能过时的内置价格猜测。
    llm_pricing_cny_per_million_json: dict[str, dict[str, float]] = Field(
        default_factory=dict,
        validation_alias=AliasChoices(
            "LLM_PRICING_CNY_PER_MILLION_JSON",
            "llm_pricing_cny_per_million_json",
        ),
    )

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
        validation_alias=AliasChoices("RERANK_API_KEY", "DASHSCOPE_API_KEY", "rerank_api_key"),
    )
    # qwen3-rerank uses the Cohere-style compatible endpoint.  The older
    # DashScope input/output envelope remains available for existing deployments.
    rerank_api_format: Literal["compatible", "dashscope_native"] = "compatible"
    rerank_base_url: str = ""
    rerank_model: str = "qwen3-rerank"
    rerank_instruct: str = (
        "Given an e-commerce shopping or support query, rank the candidate "
        "passages by relevance to the user's intent."
    )
    rerank_timeout: int = 20
    rerank_top_n: int = 6
    # P1-2：为 True 时 validate_runtime() 将缺失 rerank key 视为致命错误。
    # _rerank() 缺失时静默降级为 RRF；该标志让降级在生产环境变得显式可见。
    rerank_required: bool = False

    # Governed visual product search. Missing cloud credentials degrade only
    # this capability; the rest of the mall must remain available.
    visual_search_enabled: bool = True
    visual_index_consumer_enabled: bool = True
    visual_api_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "VISUAL_API_KEY",
            "EMBEDDING_API_KEY",
            "DASHSCOPE_API_KEY",
            "visual_api_key",
        ),
    )
    visual_grounding_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    visual_grounding_model: str = "qwen3-vl-plus"
    visual_grounding_timeout_seconds: float = 8.0
    visual_embedding_url: str = (
        "https://dashscope.aliyuncs.com/api/v1/services/embeddings/"
        "multimodal-embedding/multimodal-embedding"
    )
    visual_embedding_model: str = "qwen3-vl-embedding"
    visual_embedding_dimensions: int = 1024
    visual_embedding_timeout_seconds: float = 10.0
    visual_rerank_api_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "VISUAL_RERANK_API_KEY",
            "RERANK_API_KEY",
            "DASHSCOPE_API_KEY",
            "visual_rerank_api_key",
        ),
    )
    # Empty means: derive the native VL endpoint from RERANK_BASE_URL's
    # Workspace host. Text and VL rerank use different paths on the same host.
    visual_rerank_base_url: str = ""
    visual_rerank_model: str = "qwen3-vl-rerank"
    visual_rerank_timeout_seconds: float = 12.0
    visual_provider_deadline_seconds: float = 20.0
    visual_index_alias: str = "aishop_visual_products"
    visual_index_prefix: str = "aishop_visual_products_v"
    visual_index_model_version: str = "qwen3-vl-embedding-1024-v1"
    visual_embedding_min_cosine: float = 0.45
    visual_image_recall_size: int = 60
    visual_fused_recall_size: int = 40
    visual_text_recall_size: int = 20
    visual_rerank_candidate_size: int = 12
    visual_result_size: int = 6
    visual_max_subjects: int = 5
    visual_query_max_dimension: int = 1536
    visual_query_max_bytes: int = 10 * 1024 * 1024
    visual_index_exchange: str = "rag.exchange"
    visual_index_queue: str = "visual.index.queue"
    visual_index_dead_letter_queue: str = "visual.index.dlq"
    visual_index_routing_key: str = "rag.queue"
    visual_index_consumer_concurrency: int = 1
    visual_index_max_retries: int = 3
    visual_index_retry_backoff_seconds: float = 2.0
    visual_index_backfill_on_start: bool = True

    mysql_host: str = "localhost"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_password: str = "123456"
    mysql_database: str = "aishop_agent"
    analytics_mysql_host: str = "localhost"
    analytics_mysql_port: int = 3306
    analytics_mysql_user: str = ""
    analytics_mysql_password: str = ""
    analytics_mysql_database: str = "aishop_admin"
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
    # 租约必须短于总处理 deadline：正常 Worker 持续续租；Worker 崩溃后，其他
    # Worker 才能在 deadline 内接管。若 lease >= deadline，接管时任务必然已经过期。
    agent_task_lease_seconds: int = 30
    # MQ 发布前的 DISPATCHING 预占超时。只有该状态超时才允许恢复重发；
    # 已由 RabbitMQ confirm 的 QUEUED 任务不会被周期复制。
    agent_task_dispatch_timeout_seconds: int = 30
    agent_task_recovery_interval_seconds: int = 5
    agent_user_lock_ttl_seconds: int = 180
    # B1：悬挂在 EXECUTING 的待确认动作补终态（执行方崩溃后不再永久"处理中"）。
    pending_action_stale_seconds: int = 600
    pending_action_reconcile_interval_seconds: int = 300
    pending_action_reconcile_max_attempts: int = 6
    pending_action_reconcile_deadline_seconds: int = 3600
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
    # rerank 之后的归一化相关性（0~1）。0.65 来自锁定的 34 条 RAG 集在
    # 2026-08-06 的阈值扫描：Recall@10/MRR=0.9167，拒答 F1=0.9524。
    # 完整数据哈希和回归下限见 scripts/rag_golden.lock.json。
    rag_evidence_min_relevance: float = 0.65
    # 无 rerank 时的兜底闸门，单位是 RRF 融合分而不是任何引擎的原始分。
    # RRF 的全部意义就是丢掉不可比的原始分只留排名，所以这道闸门只能用排名表达：
    # "至少在某一路里进了前 N 名"。1/(60+N) 是 RRF 的定义式，N 越大越宽松。
    # 旧值 N=10：阈值 ≈ 0.0143，最高分 ≈ 0.0164（rank 1），相当于"进前10都通过"
    # ——几乎不过滤任何内容。新值 N=3：阈值 ≈ 0.0159，要求至少一路进前 3 才算有证据。
    rag_evidence_min_rrf_rank: int = 3
    # P1-3 意图→FAQ 类别过滤映射（键为 IntentKind.value 字符串）。
    # 配置后，对应意图的 RAG 检索仅召回指定类别的 FAQ，减少跨类别干扰。
    # 示例（.env 里设为 JSON 字符串）：
    #   RAG_INTENT_CATEGORY_MAP='{"REFUND":["退换货","售后"],"QUERY_LOGISTICS":["物流"]}'
    # 空字典（默认）= 不启用类别过滤。
    rag_intent_category_map: dict[str, list[str]] = {}
    # P2-3 A/B testing for RAG retrieval strategies.
    # ab_test_buckets: 0 or 1 = disabled; 2 = A/B; 3 = A/B/C …
    # Bucket "A" is always the unmodified baseline.
    # Per-bucket param overrides are specified as a JSON dict:
    #   AB_TEST_CONFIG='{"B":{"rag_top_k":20,"rerank_top_n":10}}'
    ab_test_buckets: int = 0
    ab_test_config: dict[str, dict] = {}
    # Bounded RAG orchestration. ``agentic_rag`` remains only for old deployments:
    # when it is explicitly configured and RAG_MODE is absent, true maps to agentic
    # and false maps to prefetch. New environments default to conditional.
    rag_mode: Literal["prefetch", "conditional", "agentic"] = "conditional"
    agentic_rag: bool = False
    # ES requires num_candidates >= k. Raising it trades latency for recall, so
    # keep a floor rather than deriving it from k alone: at k=15 a bare 2x gives
    # the HNSW search very little room to escape a local minimum.
    knn_num_candidates_factor: int = 3
    knn_num_candidates_min: int = 100
    rag_cache_ttl_seconds: int = 30 * 60
    # B2：语义缓存命中按此比例抽样进盲评队列（1% 命中会被记录，
    # 离线抽样看误报率——行业建议"命中率突降按事故处理，误报率按周评审"）。
    rag_cache_sample_rate: float = 0.01
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
    # Multi-Agent Harness and governed admin analytics are the primary runtime.
    multi_agent_enabled: bool = True
    data_analyst_enabled: bool = True
    # Agentic Commerce v2 is deliberately a single serving path. These switches
    # exist for an operational kill switch, not to keep a second recommendation
    # implementation alive indefinitely.
    shopping_decision_v2_enabled: bool = True
    outcome_ledger_enabled: bool = True
    after_sales_policy_engine_enabled: bool = True
    inventory_ops_enabled: bool = True
    operational_recommendations_enabled: bool = True
    shopping_mission_active_hours: int = 24
    shopping_mission_max_clarifications: int = 2
    shopping_offer_ttl_seconds: int = 300
    shopping_decision_max_results: int = 6
    commerce_outcome_retention_days: int = 180
    commerce_outcome_attribution_days: int = 30
    multi_agent_specialist_max_rounds: int = 2
    multi_agent_specialist_timeout_seconds: int = 12
    analytics_max_rows: int = 200
    analytics_max_days: int = 90
    analytics_query_timeout_ms: int = 3000
    analytics_model_timeout_seconds: int = 10
    analytics_request_timeout_seconds: int = 45
    graph_checkpoint_ttl: int = 3600
    graph_checkpoint_prefix: str = "mall:agent:graph:ckpt"

    intent_use_llm: bool = True
    intent_rule_fallback: bool = True
    intent_handoff_confidence: float = 0.55
    order_query_lookback_days: int = 90
    force_mcp_on_llm_skip: bool = True

    @model_validator(mode="after")
    def validate_internal_contracts(self) -> "Settings":
        if "rag_mode" not in self.model_fields_set and "agentic_rag" in self.model_fields_set:
            self.rag_mode = "agentic" if self.agentic_rag else "prefetch"
        if self.embedding_dimensions != self.es_vector_dimensions:
            raise ValueError("EMBEDDING_DIMENSIONS and ES_VECTOR_DIMENSIONS must be identical")
        if self.es_vector_dimensions != 1024:
            raise ValueError("AI_Shop vector contract requires 1024 dimensions")
        if not self.es_vector_field.strip():
            raise ValueError("VECTOR_FIELD must not be empty")
        if self.compress_token_threshold >= self.working_token_budget:
            raise ValueError("COMPRESS_TOKEN_THRESHOLD must be less than WORKING_TOKEN_BUDGET")
        if self.analytics_max_rows < 1 or self.analytics_max_rows > 200:
            raise ValueError("ANALYTICS_MAX_ROWS must be between 1 and 200")
        if self.analytics_max_days < 1 or self.analytics_max_days > 90:
            raise ValueError("ANALYTICS_MAX_DAYS must be between 1 and 90")
        if self.analytics_query_timeout_ms < 100 or self.analytics_query_timeout_ms > 10_000:
            raise ValueError("ANALYTICS_QUERY_TIMEOUT_MS must be between 100 and 10000")
        if self.analytics_model_timeout_seconds < 1 or self.analytics_model_timeout_seconds > 30:
            raise ValueError("ANALYTICS_MODEL_TIMEOUT_SECONDS must be between 1 and 30")
        if self.analytics_request_timeout_seconds < self.analytics_model_timeout_seconds:
            raise ValueError(
                "ANALYTICS_REQUEST_TIMEOUT_SECONDS must cover at least one model stage"
            )
        if self.analytics_request_timeout_seconds > 180:
            raise ValueError("ANALYTICS_REQUEST_TIMEOUT_SECONDS must not exceed 180")
        if not 30 <= self.admin_assertion_max_age_seconds <= 300:
            raise ValueError("ADMIN_ASSERTION_MAX_AGE_SECONDS must be between 30 and 300")
        if self.admin_assertion_nonce_ttl_seconds < self.admin_assertion_max_age_seconds:
            raise ValueError("ADMIN_ASSERTION_NONCE_TTL_SECONDS must cover the signature window")
        if len(self.pilot_identity_hmac_secret.encode("utf-8")) < 16:
            raise ValueError("PILOT_IDENTITY_HMAC_SECRET must be at least 16 bytes")
        if len(self.privacy_export_signing_secret.encode("utf-8")) < 16:
            raise ValueError("PRIVACY_EXPORT_SIGNING_SECRET must be at least 16 bytes")
        if not 15 * 60 <= self.privacy_export_ttl_seconds <= 7 * 24 * 60 * 60:
            raise ValueError("PRIVACY_EXPORT_TTL_SECONDS must be between 900 and 604800")
        if not 3 <= self.multi_agent_specialist_timeout_seconds <= 30:
            raise ValueError(
                "MULTI_AGENT_SPECIALIST_TIMEOUT_SECONDS must be between 3 and 30"
            )
        if not 1 <= self.shopping_mission_active_hours <= 72:
            raise ValueError("SHOPPING_MISSION_ACTIVE_HOURS must be between 1 and 72")
        if not 0 <= self.shopping_mission_max_clarifications <= 3:
            raise ValueError("SHOPPING_MISSION_MAX_CLARIFICATIONS must be between 0 and 3")
        if not 60 <= self.shopping_offer_ttl_seconds <= 900:
            raise ValueError("SHOPPING_OFFER_TTL_SECONDS must be between 60 and 900")
        if not 1 <= self.shopping_decision_max_results <= 6:
            raise ValueError("SHOPPING_DECISION_MAX_RESULTS must be between 1 and 6")
        if not 30 <= self.commerce_outcome_retention_days <= 730:
            raise ValueError("COMMERCE_OUTCOME_RETENTION_DAYS must be between 30 and 730")
        if self.max_input_chars < 128 or self.max_input_chars > 32_000:
            raise ValueError("MAX_INPUT_CHARS must be between 128 and 32000")
        for model, pricing in self.llm_pricing_cny_per_million_json.items():
            if not str(model).strip() or not isinstance(pricing, dict):
                raise ValueError("LLM pricing requires a non-empty model and an object price")
            if set(pricing) != {"input", "output"}:
                raise ValueError(f"LLM pricing for {model} must contain exactly input and output")
            if any(
                not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0
                for value in pricing.values()
            ):
                raise ValueError(f"LLM pricing for {model} must be non-negative numbers")
        if not 1 <= self.worker_metrics_port <= 65_535:
            raise ValueError("WORKER_METRICS_PORT must be between 1 and 65535")
        if self.worker_metrics_port == self.app_port:
            raise ValueError("WORKER_METRICS_PORT must differ from APP_PORT")
        if self.episode_queue_size < 100:
            raise ValueError("EPISODE_QUEUE_SIZE must be at least 100")
        if not 1 <= self.episode_batch_size <= self.episode_queue_size:
            raise ValueError("EPISODE_BATCH_SIZE must be between 1 and EPISODE_QUEUE_SIZE")
        if self.episode_flush_interval_ms < 10:
            raise ValueError("EPISODE_FLUSH_INTERVAL_MS must be at least 10")
        if not 0 <= self.episode_success_sample_rate <= 1:
            raise ValueError("EPISODE_SUCCESS_SAMPLE_RATE must be between 0 and 1")
        if self.episode_retention_days < 1:
            raise ValueError("EPISODE_RETENTION_DAYS must be positive")
        if self.judge_timeout < 1:
            raise ValueError("JUDGE_TIMEOUT must be positive")
        if not 0 <= self.judge_sample_rate <= 1:
            raise ValueError("JUDGE_SAMPLE_RATE must be between 0 and 1")
        if not 0 <= self.judge_low_score_threshold <= 1:
            raise ValueError("JUDGE_LOW_SCORE_THRESHOLD must be between 0 and 1")
        if self.judge_queue_size < 10:
            raise ValueError("JUDGE_QUEUE_SIZE must be at least 10")
        if self.rerank_timeout < 1:
            raise ValueError("RERANK_TIMEOUT must be positive")
        if self.rerank_top_n < 1:
            raise ValueError("RERANK_TOP_N must be positive")
        if self.visual_embedding_dimensions != 1024:
            raise ValueError("VISUAL_EMBEDDING_DIMENSIONS must be 1024")
        if not 0 <= self.visual_embedding_min_cosine <= 1:
            raise ValueError("VISUAL_EMBEDDING_MIN_COSINE must be between 0 and 1")
        if not 1 <= self.visual_max_subjects <= 5:
            raise ValueError("VISUAL_MAX_SUBJECTS must be between 1 and 5")
        if not 256 <= self.visual_query_max_dimension <= 4096:
            raise ValueError("VISUAL_QUERY_MAX_DIMENSION must be between 256 and 4096")
        if not 1 <= self.visual_result_size <= self.visual_rerank_candidate_size <= 40:
            raise ValueError(
                "visual result size must be <= rerank candidate size and at most 40"
            )
        if self.visual_provider_deadline_seconds < max(
            self.visual_grounding_timeout_seconds,
            self.visual_embedding_timeout_seconds,
            self.visual_rerank_timeout_seconds,
        ):
            raise ValueError("VISUAL_PROVIDER_DEADLINE_SECONDS must cover each provider timeout")
        if self.rerank_api_key.strip():
            base_url = self.rerank_base_url.strip()
            if not self.rerank_model.strip():
                raise ValueError("RERANK_MODEL must be configured when RERANK_API_KEY is set")
            if not base_url:
                raise ValueError("RERANK_BASE_URL must be configured when RERANK_API_KEY is set")
            normalized_url = base_url.upper()
            placeholder_markers = (
                "YOUR_WORKSPACE_ID",
                "{WORKSPACEID}",
                "{WORKSPACE_ID}",
                "<WORKSPACE",
            )
            if any(marker in normalized_url for marker in placeholder_markers):
                raise ValueError("RERANK_BASE_URL still contains a workspace placeholder")
            parsed = urlparse(base_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("RERANK_BASE_URL must be an absolute HTTP(S) URL")
        if not 0 <= self.rag_cache_sample_rate <= 1:
            raise ValueError("RAG_CACHE_SAMPLE_RATE must be between 0 and 1")
        if self.agent_task_lease_seconds < 30:
            raise ValueError("AGENT_TASK_LEASE_SECONDS must be at least 30")
        if self.agent_task_lease_seconds >= self.agent_task_deadline_seconds:
            raise ValueError(
                "AGENT_TASK_LEASE_SECONDS must be less than "
                "AGENT_TASK_DEADLINE_SECONDS so a crashed task can be recovered"
            )
        if self.agent_task_dispatch_timeout_seconds < 5:
            raise ValueError("AGENT_TASK_DISPATCH_TIMEOUT_SECONDS must be at least 5")
        if self.agent_task_recovery_interval_seconds < 1:
            raise ValueError("AGENT_TASK_RECOVERY_INTERVAL_SECONDS must be positive")
        if self.agent_user_lock_ttl_seconds < 1:
            raise ValueError("AGENT_USER_LOCK_TTL_SECONDS must be positive")
        if self.pending_action_stale_seconds < 1:
            raise ValueError("PENDING_ACTION_STALE_SECONDS must be positive")
        if self.pending_action_reconcile_interval_seconds < 1:
            raise ValueError("PENDING_ACTION_RECONCILE_INTERVAL_SECONDS must be positive")
        if self.pending_action_reconcile_max_attempts < 1:
            raise ValueError("PENDING_ACTION_RECONCILE_MAX_ATTEMPTS must be positive")
        if self.pending_action_reconcile_deadline_seconds < 1:
            raise ValueError("PENDING_ACTION_RECONCILE_DEADLINE_SECONDS must be positive")
        if self.agent_worker_heartbeat_ttl_seconds < 5:
            raise ValueError("AGENT_WORKER_HEARTBEAT_TTL_SECONDS must be at least 5")
        if self.visual_index_consumer_concurrency < 1 or self.visual_index_consumer_concurrency > 4:
            raise ValueError("VISUAL_INDEX_CONSUMER_CONCURRENCY must be between 1 and 4")
        if self.visual_index_max_retries < 0 or self.visual_index_max_retries > 10:
            raise ValueError("VISUAL_INDEX_MAX_RETRIES must be between 0 and 10")
        if self.visual_index_retry_backoff_seconds < 0 or self.visual_index_retry_backoff_seconds > 60:
            raise ValueError("VISUAL_INDEX_RETRY_BACKOFF_SECONDS must be between 0 and 60")
        return self

    def validate_runtime(self) -> None:
        analytics_errors: list[str] = []
        if self.data_analyst_enabled:
            if not self.analytics_mysql_user.strip() or not self.analytics_mysql_password.strip():
                analytics_errors.append(
                    "ANALYTICS_MYSQL_USER and ANALYTICS_MYSQL_PASSWORD are required"
                )
            if self.analytics_mysql_user.strip().lower() in {
                "root",
                self.mysql_user.strip().lower(),
            }:
                analytics_errors.append("DataAnalyst must use a dedicated read-only MySQL identity")
            if self.analytics_mysql_database.strip().lower() != "aishop_admin":
                analytics_errors.append("ANALYTICS_MYSQL_DATABASE must be aishop_admin")

        if self.app_env.lower() != "production":
            if analytics_errors:
                raise ValueError(
                    "Invalid DataAnalyst configuration: " + "; ".join(analytics_errors)
                )
            return

        errors: list[str] = []
        if not self.internal_token.strip() or self.internal_token == "your-token":
            errors.append("AISHOP_INTERNAL_TOKEN must be configured")
        if (
            not self.admin_assertion_current_secret.strip()
            or self.admin_assertion_current_secret == "your-token"
        ):
            errors.append("AISHOP_ADMIN_ASSERTION_CURRENT_SECRET must be configured")
        if self.pilot_identity_hmac_secret == "development-only-pilot-secret":
            errors.append("PILOT_IDENTITY_HMAC_SECRET must be configured")
        if self.privacy_export_signing_secret == "development-only-privacy-secret":
            errors.append("PRIVACY_EXPORT_SIGNING_SECRET must be configured")
        if not self.llm_api_key.strip():
            errors.append("LLM_API_KEY must be configured")
        if not self.embedding_api_key.strip():
            errors.append("EMBEDDING_API_KEY must be configured")
        if self.rerank_required and not self.rerank_api_key.strip():
            errors.append("RERANK_API_KEY must be configured (RERANK_REQUIRED=true)")
        errors.extend(analytics_errors)
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
