# AI_Shop AI 应用求职项目证据总览

> 更新：2026-08-20（Asia/Shanghai）
>
> 证据实现基线：提交 `c501735644a72ce57b27e48bdd35fbc7a5e870ea` 与由 [evidence-manifest.json](evidence-manifest.json) 固化的实现补丁；结果 SHA、数据锁和文档一致性要求也以该 manifest 为准。
>
> 适用：AI 应用 / Agent 后端、Java + AI 业务后端，以及要求 RAG、工具调用、可靠执行和评测能力的校招岗位。

这份文档只陈述可复核的项目事实。机器可检验的路径、哈希和结果边界以 manifest 为准；本地 `benchmarks/results/` 被 Git 忽略，不能被表述为已发布基线或生产数据。

## 结论

AI_Shop 适合作为 AI 应用开发岗位的主项目，但最可信的讲法是“受控业务 Agent + Java 交易底座”，不是“已上线的大规模智能体平台”。系统把自然语言理解、已发布知识库 RAG、MCP/Java 权威查询、写操作提案、用户确认、Java 幂等执行、未知结果核对和 Episode Trace 串成一条业务状态机。

功能闭环分层成立：取消订单的本地全栈样本已经真实走通；模型驱动的政策问答也有一条真实 Provider/RAG 样本。完整 44 条 Agent v2 契约、三种编排模式的配对消融和真实用户试用仍未采集，当前 lock 的 `resultStatus=NOT_COLLECTED`，因此不能把两个单样本成功率宣传为总体 Agent TSR、P95 或线上业务效果。

## 证据分层

| 等级 | 当前证据 | 可以怎么说 | 不能怎么说 |
|---|---|---|---|
| E0 源码与契约 | LangGraph/MCP/Java 边界、44 条冻结 Agent v2 契约、定向单元测试 | 已实现受控写操作、确认和恢复逻辑 | 已有完整 live 成绩 |
| E1 确定性合成 | Commerce `27/27`、安全 `18/18`、Search/RAG contract `162/162` | 生产决策内核和安全/数据契约已回归 | 在线模型准确率、真实延迟或用户收益 |
| E2 本地全栈 | 取消订单 `1/1`，`1067 ms` | Agent API 到 Worker、MCP、Java 写接口和确认终态已走通 | LLM 性能，或总体售后成功率 |
| E3 配置真实 Provider | 政策问答 `1/1`，`deepseek-v4-flash`，RAG Provider Trace 完整 | 真实 LLM/Embedding/Rerank 链路可复核 | `n=1` 的 P95、SLO 或总体质量 |
| E4 授权真实用户 | 未采集 | 真实用户指标尚无数据 | FCR、CTR/CVR、GMV 提升或生产体验 |

## 当前可复核结果

### 1. 受控取消订单：本地全栈闭环

运行 `agent-v2-adaptive-c501735-20260820-cancel-golden-r6` 的冻结 case 为 `live-cancel-confirmed-012`，结果为 `1/1` 通过，严重安全违规为 `0`。

- 路径：Agent API -> RabbitMQ Worker -> LangGraph -> MCP -> pending action -> 用户确认 -> Java 写接口。
- 编排：自适应模式选择 `workflow`，原因是参数完整的确定性业务路径。
- 终态事件：`ACTION_PROPOSED -> ACTION_CONFIRMED_BY_USER -> ACTION_TERMINAL`。
- 持久状态：`CANCEL_ORDER` 的 pending action 为 `CONFIRMED`，订单状态从 `0` 变为 `4`。
- 单样本端到端延迟：`1067 ms`；该 Workflow 未调用 LLM，输入/输出 token 均为 `0`。
- 脱敏 Trace：[`report.md`](evidence/agent-traces/agent-v2-cancel-r6-20260820/report.md)、[`traces.json`](evidence/agent-traces/agent-v2-cancel-r6-20260820/traces.json)、[`SHA256SUMS`](evidence/agent-traces/agent-v2-cancel-r6-20260820/SHA256SUMS)。运行 ID、订单标识和 action token 已做指纹化或删除。

这里的 `0` token 只说明该条确定性 Workflow 没有调用模型；`totalCostCny=0.0` 不能被写成真实模型成本为零，当前成本口径是 `UNPRICED`。

### 2. 政策问答：真实 Provider/RAG 单样本

运行 `agent-v2-adaptive-c501735-20260820-rag-policy-confirmation-r5` 的 case `live-confirmation-policy-020` 为 `1/1` 通过。

- 编排：`single_agent`；模型：`deepseek-v4-flash`；LLM 调用 `1` 次。
- 用量：`4847` input + `224` output = `5071` tokens。
- 延迟：`3237 ms`。
- 检索：Elasticsearch BM25 `1`、Vector `1`、Embedding 成功 `1`、Rerank 成功 `1`、fallback `0`、source refs `2`。
- 写工具：`0`；严重安全违规：`0`；Provider completeness：`1.0`。

它证明真实 Provider 的单轮政策问答链路存在，不证明完整任务集质量，也不能从一个样本推导 P95 或模型成本。

### 3. 必须保留的失败诊断

四个 `live-refund-policy-018` 结果没有被删除或改写：

| 运行 | 现象 | 结论 |
|---|---|---|
| `rag-policy-r1` | OpenAI-compatible 工具消息顺序错误 | 已定位为协议组装问题并修复 |
| `rag-policy-r2` | Worker 重启窗口超时 | 基础设施干扰，不纳入模型质量结论 |
| `rag-policy-r3` | Provider 路径成功但 `9965` tokens 超过 `8000` 门禁 | 保留失败，不能靠改报告刷绿 |
| `rag-policy-r4` | `9992` tokens，且“七天退货政策”不在发布知识库权威语料中 | 真实知识覆盖 Bad Case；应补发布语料/评测，而不是降低证据阈值 |

确认通过的 `live-confirmation-policy-020` 是另一条可被当前知识库支持的政策问题，不能覆盖上表的退款政策 Bad Case。

## 历史 Search/RAG 性能与质量证据

历史 formal `SYNTHETIC + local-live` 结果保留在 `benchmarks/evidence/`，详细数字见 [性能与质量证据_20260820.md](性能与质量证据_20260820.md)。它们有真实 Provider 调用，但不是本轮 Agent v2 的新总体成绩。

- Search v2：中文 `600` 商品 / `240` 查询，WANDS `42,994` 商品 / `202` 查询 / `32,919` 有效判断；正式质量门禁为 `FAILED_RETAINED`，其中 ProductService 首次 Recall@10 仅 `0.3778`。
- RAG retrieval v4：`264` 条，fresh `48` 条；fresh Recall@5 `0.8056`、MRR@10 `0.75`、NDCG@5 `0.7645`，正式门禁为 `FAILED_RETAINED`。
- RAG generation v4：`60/60` 执行、运行时错误 `0`、严重安全违规 `0`，但 task success rate `0.65`（`39/60`），人工盲评仍为 `HUMAN_REVIEW_PENDING`。

这些失败是项目可信度的一部分：后续的 exposed-holdout replay 或 targeted regression 只能证明已知问题的修复，不能替代新的 fresh 结论。

## 面试中可成立的项目叙述

“我把电商 AI 客服收敛为受控售后闭环：模型负责理解、检索和结构化提案，Java 仍负责订单权威事实、身份/归属/状态校验和幂等写入。用户确认后才执行；如果远端结果未知，状态不会被伪造为成功，而是进入 `INCONCLUSIVE` 并按核对边界转 `MANUAL_REVIEW`。我用 Episode、Provider Trace、pending-action 状态和冻结任务契约来判分。当前已经有一个取消订单的本地全栈闭环和一个真实 Provider/RAG 政策问答样本；完整 44 条任务和真实用户层仍明确标为未采集。”

## 仍需补足的优先项

1. 在同一隔离 fixture、模型版本和 Provider 指纹下完成完整 44 条 Agent v2，并保留所有失败 case。
2. 以同一数据/fixture/模型完成 `workflow`、`single_agent`、`multi_agent` 的配对消融，才比较成功率、token 和延迟。
3. 为“七天退货政策”等缺失事实补发布版知识库、canonical label 和新的未见集；不能用降低证据门槛替代知识治理。
4. 接入可信人民币定价与账本，区分模型、Embedding、Rerank 和重试成本；当前统一写作 `UNPRICED`。
5. 完成两名独立 reviewer 的盲评，以及经授权的真实用户试用；未完成前不写线上效果。
6. 提高 Java 交易/售后核心包的覆盖率，并补长时间、并发和故障恢复数据。
