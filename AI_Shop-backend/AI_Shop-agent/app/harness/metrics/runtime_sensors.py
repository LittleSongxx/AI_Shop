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

STREAM_TOKENS = Counter("agent_stream_tokens_total", "流式输出 token 数")

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
