from prometheus_client import Counter, Gauge, Histogram

INTENT_TOTAL = Counter(
    "agent_intent_total",
    "意图识别次数",
    ["intent", "source"],
)

RAG_SEARCH_TOTAL = Counter(
    "agent_rag_search_total",
    "RAG 检索结果",
    ["result", "mode"],
)
RAG_LATENCY = Histogram(
    "agent_rag_latency_seconds",
    "RAG 检索延迟",
    buckets=[0.01, 0.05, 0.1, 0.3, 0.5, 1, 2, 5, 10],
)

LLM_LATENCY = Histogram(
    "agent_llm_latency_seconds",
    "LLM 调用延迟",
    buckets=[0.5, 1, 2, 3, 5, 10, 30],
)

CIRCUIT_STATE = Gauge(
    "agent_circuit_state",
    "熔断器状态 0=closed 1=open 2=half_open",
    ["breaker"],
)

TOOL_CALL_TOTAL = Counter(
    "agent_tool_call_total",
    "工具调用次数",
    ["tool", "status"],
)

# 口径修正：流式计数统计的是字符数而不是模型 token（真实 token 需要
# provider 返回 usage 字段）。新增语义正确的指标；旧名保留并继续累计，
# 避免 Grafana 面板断数据——但面板标题已改为"字符"，成本类告警不应依赖它。
STREAM_CHARS = Counter("agent_stream_chars_total", "流式输出字符数（非 token，成本告警勿用）")
STREAM_TOKENS = Counter(
    "agent_stream_tokens_total",
    "deprecated：历史实现实际计数为字符数，仅保留兼容，新代码请用 agent_stream_chars_total",
)

AGENT_TASK_TOTAL = Counter(
    "agent_task_total",
    "Agent 异步任务处理结果",
    ["queue", "result"],
)
AGENT_TASK_INFLIGHT = Gauge(
    "agent_task_inflight",
    "Agent 各优先级队列正在处理的任务数",
    ["queue"],
)
AGENT_TASK_BACKLOG = Gauge(
    "agent_task_backlog",
    "Agent 数据库任务积压量",
)

# P0-2c：checkpoint 写失败次数。写失败意味着"这次运行无法恢复"——
# 不该静默降级成进程内 checkpoint 假装可恢复。
CHECKPOINT_PERSIST_FAILURES = Counter(
    "agent_checkpoint_persist_failures_total",
    "图 checkpoint 持久化到 Redis 失败的次数",
)

# A4：LLM 真实 usage（provider 返回的 token 数）。成本核算唯一可信来源
# （流式字符数不是 token，见 STREAM_CHARS 注释）。
LLM_TOKEN_TOTAL = Counter(
    "agent_llm_tokens_total",
    "LLM token 用量（provider usage 字段）",
    ["kind"],
)
LLM_CALL_TOTAL = Counter(
    "agent_llm_call_total",
    "LLM 调用次数（按成功/失败与 fallback 标记）",
    ["model", "fallback", "result"],
)

# A5：转人工决策的可观测信号。原因分布必须可查询——
# "为什么转人工"和"转人工率"同样重要（误转/漏转都比"少转"难发现）。
HANDOFF_TOTAL = Counter(
    "agent_handoff_total",
    "转人工决策（HANDOFF / HANDOFF_SUGGESTED）",
    ["reason"],
)
