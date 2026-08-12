import time
from collections.abc import Iterator
from contextlib import contextmanager

from prometheus_client import Counter, Gauge, Histogram

INTENT_TOTAL = Counter(
    "agent_intent_total",
    "意图识别次数",
    ["intent", "source"],
)
INTENT_SCHEMA_TOTAL = Counter(
    "agent_intent_schema_total",
    "LLM 意图结构化输出结果",
    ["result"],
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
RAG_CHANNEL_CONTAMINATED = Counter(
    "agent_rag_channel_contaminated_total",
    "知识库片段命中注入话术被检疫的次数（按命中规则聚合，告警信号）",
    ["rules"],
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
    ["kind", "model", "fallback"],
)
LLM_COST_CNY = Counter(
    "agent_llm_cost_cny",
    "按配置单价计算的 LLM 人民币成本",
    ["kind", "model", "fallback"],
)
LLM_UNPRICED_TOKEN_TOTAL = Counter(
    "agent_llm_unpriced_tokens_total",
    "未配置价格的 LLM token 数",
    ["kind", "model", "fallback"],
)
LLM_CALL_TOTAL = Counter(
    "agent_llm_call_total",
    "LLM 调用次数（按成功/失败与 fallback 标记）",
    ["model", "fallback", "result"],
)

AGENT_STAGE_NAMES = frozenset(
    {"queue_wait", "intent", "rag", "first_token", "tool", "generation", "total"}
)
AGENT_STAGE_LATENCY = Histogram(
    "agent_stage_latency_seconds",
    "Agent 固定低基数阶段时延",
    ["stage"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120],
)

ORDER_REFERENCE_TOTAL = Counter(
    "agent_order_reference_total",
    "自然语言订单引用解析结果",
    ["intent", "outcome"],
)
ORDER_REFERENCE_LATENCY = Histogram(
    "agent_order_reference_latency_seconds",
    "自然语言订单引用解析延迟",
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10],
)
ORDER_SELECTION_TOTAL = Counter(
    "agent_order_selection_total",
    "订单候选卡选择结果",
    ["intent", "outcome"],
)
VISUAL_SELECTION_TOTAL = Counter(
    "agent_visual_selection_total",
    "图片商品主体选择结果",
    ["outcome"],
)
VISUAL_INDEX_EVENT_TOTAL = Counter(
    "agent_visual_index_event_total",
    "视觉商品索引事件处理结果",
    ["result"],
)
VISUAL_INDEX_DOCUMENT_TOTAL = Counter(
    "agent_visual_index_documents_total",
    "视觉商品索引写入文档数",
)


def observe_agent_stage(stage: str, seconds: float) -> None:
    if stage not in AGENT_STAGE_NAMES:
        raise ValueError(f"unsupported agent latency stage: {stage}")
    AGENT_STAGE_LATENCY.labels(stage=stage).observe(max(0.0, float(seconds)))


@contextmanager
def measure_agent_stage(stage: str) -> Iterator[None]:
    started = time.perf_counter()
    try:
        yield
    finally:
        observe_agent_stage(stage, time.perf_counter() - started)

# A5：转人工决策的可观测信号。原因分布必须可查询——
# "为什么转人工"和"转人工率"同样重要（误转/漏转都比"少转"难发现）。
HANDOFF_TOTAL = Counter(
    "agent_handoff_total",
    "转人工决策（HANDOFF / HANDOFF_SUGGESTED）",
    ["reason"],
)

EPISODE_EVENT_TOTAL = Counter(
    "agent_episode_event_total",
    "Application-level Agent episode events by persistence result",
    ["event", "result"],
)
EPISODE_DROPPED_TOTAL = Counter(
    "agent_episode_dropped_total",
    "Episode events intentionally dropped from the fail-open writer",
    ["reason"],
)
EPISODE_QUEUE_DEPTH = Gauge(
    "agent_episode_queue_depth",
    "Pending events in the in-process Episode persistence queue",
)
EPISODE_WRITE_LATENCY = Histogram(
    "agent_episode_write_latency_seconds",
    "Episode batch persistence latency",
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5],
)

JUDGE_EVALUATION_TOTAL = Counter(
    "agent_judge_evaluation_total",
    "Asynchronous answer judge outcomes",
    ["result"],
)
JUDGE_DROPPED_TOTAL = Counter(
    "agent_judge_dropped_total",
    "Judge requests dropped from the fail-open shadow path",
    ["reason"],
)
JUDGE_QUEUE_DEPTH = Gauge(
    "agent_judge_queue_depth",
    "Pending requests in the shadow judge queue",
)
JUDGE_LATENCY = Histogram(
    "agent_judge_latency_seconds",
    "Shadow judge provider latency",
    buckets=[0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30],
)
JUDGE_SCORE = Histogram(
    "agent_judge_score",
    "Shadow judge score by quality dimension",
    ["dimension"],
    buckets=[0, 0.25, 0.5, 0.65, 0.75, 0.9, 1],
)
RESPONSE_VERIFIER_TOTAL = Counter(
    "agent_response_verifier_total",
    "Deterministic final-response verification outcomes",
    ["result", "rule"],
)

EPISODE_TERMINAL_TOTAL = Counter(
    "agent_episode_terminal_total",
    "Agent Episode terminal states",
    ["status"],
)
BADCASE_CANDIDATE_TOTAL = Counter(
    "agent_badcase_candidate_total",
    "Badcase candidate signals received",
    ["source", "severity"],
)
DATASET_REVIEW_TOTAL = Counter(
    "agent_dataset_review_total",
    "Human Episode dataset review decisions",
    ["decision"],
)
REGRESSION_REPLAY_TOTAL = Counter(
    "agent_regression_replay_total",
    "Deterministic regression replay results",
    ["result"],
)
