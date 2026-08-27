# AI_Shop 项目收口交付

> 版本边界：当前客服人工语义主证据为 v56 冻结 HTTP replay 的 A/B+第三人仲裁包；人工拥有最终决策权，AI 只辅助文字和落盘，证据等级为 `HUMAN_APPROVED_AI_ASSISTED`。
> 本文把当前源码 contract、历史只读评测、当前已见集人工证据和尚未完成的 unseen/生产证据分开记录；不把离线结果写成生产指标。

## 一句话结论

AI_Shop 已形成“Java 权威业务状态 + Python Agent 编排/检索 + MCP 工具边界 + 可审计证据与验证器”的闭环。动态订单事实、操作资格、引用证据链、退款/支付/售后边界和商品硬约束已经过多轮真实 HTTP 回放与人工复评。当前已见 120 条的 v56 人工联合质量为 `120/120`，但这只证明同集 badcase 收口，不证明 unseen 泛化或生产质量。

### 2026-08-25 v20 HTTP 与人工答案收口（历史阶段）

在 v14 补齐 `java-gateway`、`agent-readiness`、`product-catalog` 和 `agent-write-fixture-boundary` 四项 regression preflight 依赖后，又针对订单事实、售后资格、搜索约束、退款政策路由和 canonical fact hint 做了定向修复，最终在同一源码指纹下生成 `customer-service-http-v20-20260825`。v20 真实 HTTP 请求完成 `60/60`、行为契约 `11/11`、handoff accuracy `1.0`、hard constraint violation `0`、fixture cleanup failure `0`；冻结运行包见 [v20 HTTP report](../../AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/customer-service/customer-service-http-v20-20260825/report.md)。

v20 本地 full-stack P50/P95/P99 为 `1011.116/2010.016/7268.746 ms`，边界明确为 `LOCAL_FULL_STACK_NOT_PRODUCTION_SLO`；Provider cost 为 `UNPRICED`，不能写成零成本。运行时 verifier PASS、引用结构有效和行为契约通过只说明执行与安全边界，不是人工答案质量。

两名真实 reviewer 已独立填写并封存 60 条 A/B 表，案件级完全一致 `56/60=93.33%`；4 条分歧由独立第三人 `reviewer-c` 仲裁。最终只读包状态为 `HUMAN_REVIEWED_ADJUDICATED`：答案正确 `57/60=95.00%`、可计分引用支持 `25/36=69.44%`、转人工适当 `60/60=100%`、unsafe answer `1/60=1.67%`、联合质量 `49/60=81.67%`。完整指标、CI、标签和 11 条 badcase 见 [v20 最终人工证据](../../AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/customer-service/customer-service-http-v20-answer-review-adjudicated-20260825/final-report.md)，双评父证据见 [v20 pending 包](../../AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/customer-service/customer-service-http-v20-answer-review-pending-adjudication-20260825/)。源 HTTP report 中生成时的 `PENDING_HUMAN_REVIEW` 保持不可变，外部 final package 才表示人工生命周期完成；`releaseGateEligible=false` 仍然正确。

v20 另保留完全隔离的模型辅助诊断，状态为 `MODEL_ASSISTED_DIAGNOSTIC_NOT_HUMAN_REVIEW`；60 条中 4 条模型判断分歧，没有形成可证实的新高价值修复项。模型诊断未写入上述人工指标，也没有替代任何 A/B 或仲裁标签。

### 2026-08-26 v27 历史人工答案收口

v27 针对前一轮动态事实、引用和约束修复后的源码执行了完整 60 条 HTTP replay。两名 reviewer 独立盲审，案件级一致 `58/60`；`2` 条由独立 `reviewer-c` 仲裁。最终 immutable package 为 [v27 人工证据](../../AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/customer-service/customer-service-http-v27-full-quality-fixes-answer-review-adjudicated-20260826/final-report.md)，pending parent、sealed 表和 `SHA256SUMS` 均保留。

最终答案正确 `59/60=98.33%`（Wilson `[91.14%,99.71%]`），可计分引用支持 `35/36=97.22%`（`[85.83%,99.51%]`），联合质量 `59/60=98.33%`（`[91.14%,99.71%]`），unsafe `0/60`（95% Wilson 上界约 `6.02%`）。`releaseGateEligible=false`；这些是冻结回放人工语义指标，不是线上准确率、CSAT/FCR、unseen 泛化或发布门禁。

唯一 v27 badcase 为 `cs-gold-v1-001`：用户指定“索尼 WH-1000XM6”，回答摘要丢失具体型号，现有来源只能支持完整型号查询未命中，不能支持扩大的品牌/预算结论。该问题在后续新 run和新人工包中关闭，v27 历史包保持不变。源 report 位于 ignored `run/`，完整 SHA-256 为 `f8724dac5c951b30a046dbe30ad3a4ce65b2a60a935aaf42a5720406fb172a61`。

订单事实源码 contract 检查为 `9/9`；早期完整 Python 回归为 `1417 passed, 7 skipped`，订单模块 Maven 为 `104 tests, 0 failures`。随后客服 v2.1、v43–v56、来源审计和证据工具扩展后的完整 Python 回归记录为 `1635 passed, 7 skipped, 0 failed`。这些工程测试与 HTTP/人工语义证据口径彼此独立，均不能宣称线上答案质量或吞吐提升。

### 2026-08-26–27 v2.1 审计与 v43→v54→v56 优化收口

- 标签层：对 25 条 taxonomy/slot 政策争议做 A/B 重审，20 条一致、5 条仲裁，生成 120 条 v2.1 successor（SHA-256 `02a6dacc6a2aadb88c6dfb60bf7a74e2f083fcba0f9a6e82fef38c4dfa82caf3`）。来源独立复核的 slot exact 仅 `0.50`，因此这套数据仍只作开发诊断。
- v43 基线：120 条生产路径执行和 22/22 契约通过；人工答案 `107/120`、引用 `66/70`、转人工 `118/120`、unsafe `1/120`、联合 `105/120`，共 15 条 badcase。
- v54 复评：定向修复动态事实、转人工、退款/支付、搜索约束与引用边界；全量执行 `120/120`、契约 `23/23`，人工联合 `113/120`，剩余 7 条 badcase。
- v55/v56 收口：v55 已知 7 条定向回放 `7/7`；v56 使用知识库 v3 完成 `120/120` 执行、`29/29` 契约、引用结构违例 `0`。A/B 完全一致 `118/120`，2 条由第三人仲裁；最终答案 `120/120`、引用 `67/67`、转人工 `120/120`、unsafe `0/120`、联合 `120/120`、badcase `0`。
- Search 定向回放：已知 10 条上 Recall@10 `0.833333→0.966667`、micro Recall `0.777778→0.944444`、MRR `0.725→0.775`、NDCG `0.650292→0.759759`，硬约束违例 `0`。这是同集 paired 证据，不是新 final。
- 证据治理：原始人工回传统一封存在 `evaluation-evidence/intake-archive/`；执行包、pending parent、final package、哈希和 claim boundary 由 `docs/evidence-manifest.json` 跨包校验。临时 `holdout/` 收件箱在确认归档字节一致后清理，不进入仓库。

## 主架构

```mermaid
flowchart LR
    U[用户/前端] --> G[Java Gateway\n认证·限流·路由]
    G --> API[Python Agent API\nHTTP/WebSocket]
    API --> W[Agent Worker\n队列·租约·总 deadline]
    W --> O[LangGraph 编排\n意图·槽位·确认·验证]
    O --> MCPc[MCP Streamable Client]
    MCPc --> MCP[MCP Server\n工具契约与身份边界]
    MCP --> J[Java 内部业务 API]
    J --> DB[(MySQL\n订单/商品/库存/支付)]
    J --> R[(Redis\n幂等/锁/Checkpoint)]
    J --> MQ[(RabbitMQ/Outbox\n异步事务事件)]
    O --> RET[BM25 + 向量 + RRF/Rerank]
    RET --> ES[(Elasticsearch/向量索引)]
    O --> LLM[LLM Provider\n仅生成解释/澄清]
    O --> TRACE[Episode/Trace\nsourceRefs·state diff·usage]
```

### 边界与所有权

| 部件 | 负责什么 | 明确不负责什么 |
|---|---|---|
| Java Gateway/服务 | 用户身份、订单/商品/库存/支付/退款、事务写入、幂等、最终状态 | 不把 LLM 文本当作业务事实 |
| Python Agent API/Worker | 意图与槽位、路由、LangGraph 编排、确认卡、响应验证、Episode | 不直接写订单、库存或支付 |
| MCP Server | 受控工具入口、工具契约、委托身份校验、调用 Java | 不自行推断订单资格或库存存在性 |
| MySQL/Redis/RabbitMQ | 持久事实、锁/Checkpoint/幂等、Outbox 与异步通知 | 不承载未经授权的模型状态变更 |
| Elasticsearch/向量索引 | 商品与知识召回、RRF/rerank 的检索证据 | 不决定订单动态事实 |
| LLM Provider | 候选集内解释、比较、澄清和自然语言生成 | 不生成商品 ID、库存、报价、订单状态或可执行资格 |

## 两条主链路

### 订单/客服链路

1. 请求进入 Agent 后先做意图、槽位和订单号解析；需要动态事实时进入订单引用解析器。
2. MCP 工具携带委托身份调用 Java `/internal/order/agent/*`。Java 重新校验用户归属，并返回订单、订单项、物流、评价、退款状态或操作资格。
3. Python 将动态来源放在 `tool_source_refs/businessSources`，与政策 RAG 来源分离。订单引用使用 `order-fact/v2`，每个可回答字段生成 `DYNAMIC_FACT` claim。
4. 响应验证器按字段检查状态、金额、商品名、规格、支付方式、数量、订单/订单项归属；只有订单号本身不能授权其它事实。
5. 取消、确认收货、评价等写提案必须有 Java `actionCapability` 决定和用户确认 token；售后资格还必须有 `decisionId/policyId/policyVersion/evaluatedAt`。
6. 最终 Episode/trace 保存工具调用、sourceRefs、验证问题码、state diff、终态和 usage；Java 写服务再次执行事务与幂等校验。

关键实现：[OrderAgentInternalController.java](../../AI_Shop-backend/AI_Shop-order/app/src/main/java/com/aishop/controller/internal/OrderAgentInternalController.java)、[evidence_refs.py](../../AI_Shop-backend/AI_Shop-agent/app/services/evidence_refs.py)、[response_verifier.py](../../AI_Shop-backend/AI_Shop-agent/app/services/response_verifier.py)、[order_reference_flow.py](../../AI_Shop-backend/AI_Shop-agent/app/graph/order_reference_flow.py)。

### 商品搜索/导购链路

1. `product_search_query` 将品牌、型号、预算、类别、排除词、比较对象拆成结构化约束。
2. BM25 与向量召回后用 RRF/rerank 合并；候选集再经过类别、品牌、预算、否定条件和 Java offer/inventory snapshot 校验。
3. 只把已返回且通过硬约束的候选交给 LLM 做解释或比较；无结果只说明“当前查询范围未命中”，不声称平台全量无货。
4. 每次搜索记录 query plan、retrieval variants、recall/rejection counts、候选快照和 hard-constraint audit，便于从 trace 定位漏召回与误过滤。

关键实现：[product_search_query.py](../../AI_Shop-backend/AI_Shop-agent/app/services/product_search_query.py)、[product_search_pipeline.py](../../AI_Shop-backend/AI_Shop-agent/app/services/product_search_pipeline.py)、[mcp_tools_service.py](../../AI_Shop-backend/AI_Shop-agent/app/services/mcp_tools_service.py)。

## 三条真实 badcase 收口

### 1. 动态商品名没有证据

**现象。** 客服回答能说出订单状态和商品名，但冻结的 `sourceRefs` 只有订单号/状态/金额，没有 `order_item.productName`。v13 人审将 `004/005/006/008/014/016/017/035` 归为引用不支持；其中有些答案逻辑上正确，问题仍是事实无法由可见来源核对。

**Trace 定位。** 在 HTTP Episode 的 `RAG_RETRIEVAL`/业务工具观察和最终 envelope 查看 `businessSources`：旧记录能看到订单 snapshot，却找不到对应 `order_item` 的 `productName` claim。代码侧对应 `tool_source_refs -> order_reference_evidence -> response_verifier` 传播链；复核素材在 [v13 仲裁 badcases](../../AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/customer-service/customer-service-http-v13-answer-review-adjudicated-20260824/badcases.jsonl)。

**根因。** Java 订单投影和 Python 引用投影只保留了订单级摘要；验证器曾把“有订单号”误当成足够的动态事实依据，模型生成的商品名因此可以混入答案。

**已实施修复。** Java 返回订单项的商品名、封面、规格、金额、数量和状态；`evidence_refs.order_refs()` 生成 `order-fact/v2` 字段级 claims，并带 `authorityBoundary`。验证器现在要求商品名与同一订单项 claim 精确匹配，错订单项选择直接返回空卡片；缺 claim 时返回 `DYNAMIC_FACT_WITHOUT_CLAIM` 和安全降级文案。

**当前回归证据。** 收口 contract 的 `order-product-claim`、`order-product-mismatch`、`selection-item-ownership` 均通过；完整报告为 `9/9`。新增回归位于 [test_response_verifier.py](../../AI_Shop-backend/AI_Shop-agent/tests/test_response_verifier.py)。

**历史人工证据。** v20 已完成新 observation、新双盲和新仲裁，可独立报告本次冻结输出的引用支持 `25/36=69.44%`；这不是与 v13/v27 的严格因果 A/B。`008/009/014/018/019/020/021/027/029/043/055` 仍被判为引用不支持，不能因结构上存在 `sourceRefs` 而从分母删除。

### 2. 待发货状态被误判为不可取消

**现象。** `cs-gold-v1-012` 的订单处于“已付款、待发货”，回答却直接说“当前不能取消”，没有重新核验资格或转人工。第三人仲裁判定答案正确性和转人工均失败，且引用不支持。

**Trace 定位。** 查看该 case 的订单 snapshot、`actionCapability` 是否被调用、`response_verifier` 的 capability issue 和最终 `handoff` 事件。历史坏例的关键缺口是只有 `order.orderStatus`，没有 Java 返回的 `CANCEL_ORDER` decision；素材见 [v13 final-report.json](../../AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/customer-service/customer-service-http-v13-answer-review-adjudicated-20260824/final-report.json) 的 `cs-gold-v1-012`。

**根因。** 把订单状态快照当成操作资格。状态到能力不是一一同构关系，且资格可能在用户确认到实际写入之间变化；没有独立的服务端 decision，模型容易过度确定地拒绝或允许操作。

**已实施修复。** Java 新增只读 `/internal/order/agent/actionCapability`，返回 `decision/action/orderId/orderItemId/reasonCode/capabilityVersion/evaluatedAt`；`action_capability_ref` 只接受 Java 业务来源。验证器同时校验动作、订单/订单项和允许/拒绝极性；缺少决定、极性不匹配或目标不匹配均阻断答案。真实写命令仍在 Java 事务/锁内二次校验。

**当前回归证据。** 收口 contract 的 `capability-allowed`、`capability-polarity-mismatch` 通过；`test_response_verifier.py` 覆盖无决定、RAG 伪造决定、动作/订单项错配。Java `OrderAgentInternalControllerTest` 也随订单模块完整测试通过（模块总计 `104` tests、`0` failures）。

**剩余限制。** 当前 contract 是离线服务独立检查，不覆盖真实数据库竞态、写入失败或人工队列延迟；生产路径仍必须在执行命令时重新核验资格并保留 `INCONCLUSIVE/MANUAL_REVIEW`。

### 3. 预算/比较对象 hard-negative 已知集改善，泛化未证

**现象。** 固定 v9 qrel 的三条难例仍未恢复：

| Case | 查询 | 未召回 | 成对回放结果 |
|---|---|---|---|
| `search-fin-v9-23-snack-100` | `100元以内旺旺雪饼和可乐零食` | `303019597302892`、`438316828084252` | Recall/MRR/NDCG delta `0` |
| `search-fin-v9-34-snack-no-wangwang` | `平价零食不要旺旺雪饼` | `303019597302892` | Recall/MRR/NDCG delta `0` |
| `search-fin-v9-47-compare-xm` | `WH-1000XM6和十周年版降噪耳机如何比较` | `350000232815799` | Recall/MRR/NDCG delta `0` |

**Trace 定位。** 在 [search-hard-negative paired cases](../../AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/search/search-hard-negative-paired-v1-20260823/cases.jsonl) 的 `candidateTrace[0]` 查看 `queryPlan.retrievalVariants`、`recallCounts`、`rejectionCounts`、`resultCount` 和 `stageLatencyMs`；坏例的 `badcases.jsonl` 明确标记 `RECALL_MISS_REMAINS`，而不是硬约束违规。

**根因。** 多对象 conjunction、否定条件和比较对象在查询解析/召回阶段被压缩，候选集合在 rerank 之前就不完整；后置硬约束可以过滤错误候选，却无法找回尚未召回的商品。

**已实施的边界修复。** 搜索链路保留显式否定、类别/品牌约束和候选审计，并加入查询拆解、比较对象保留和候选 union。同一 10 条已知难例的 v4 paired replay 中，Recall@10 `0.833333→0.966667`、micro Recall `0.777778→0.944444`、MRR `0.725→0.775`、NDCG `0.650292→0.759759`，硬约束违例仍为 `0`。qrel 和分母没有修改。

**剩余工作。** 已知集改善不等于新分布泛化。下一步只在新预注册 holdout 复验，不继续修改当前 qrel 或重刷已知集。

## 当前指标与可用性

### 本轮可归因的源码证据

| 证据 | 结果 | 口径 |
|---|---:|---|
| 订单事实/资格/响应 verifier contract | `9/9` | 当前源码的固定离线 case；不是线上答案正确率 |
| Python 全量 pytest（`shop`） | `1635 passed, 7 skipped` | 7 项均因没有真实 MySQL 8 条件跳过 |
| Python Ruff / compileall / `git diff --check` | 全部通过 | Ruff 针对 `app evaluation tests scripts/project_closure_check.py` |
| Java `mvn -pl AI_Shop-order/app -am test` | `104 tests, 0 failures` | 订单模块及其依赖 reactor；RabbitMQ 外部集成条件跳过不计入通过数 |
| evaluation registry validate | `valid=true` | 历史数据锁、release 和 SHA-256 元数据完整 |
| regression preflight（v14） | `PASS` | 四项依赖均 ready；fixture scope 为 `LOCAL_EVALUATION_ONLY`，详见 [preflight-regression-20260825-r2](closure-evidence/preflight-regression-20260825-r2.json) |

### 历史只读质量观察（不能归因于本轮）

| 域 | 最新可复核观察 | 可怎么说 | 不能怎么说 |
|---|---|---|---|
| Search v9 | Recall@10 macro `0.962121`（44 query），micro `52/56`；MRR `0.937500`；NDCG `0.920521`；硬约束违规 `0` | 小商品集离线检索质量与约束门禁 | 线上 CTR/CVR、个性化收益、生产 SLO |
| RAG v9 | answerable Recall@5 `29/29`，lexical grounded/citation `50/50` | 封闭知识库的可审计下界 | 人工语义准确率、开放域泛化 |
| Agent v9 | 25 case/200 trials，`pass^8=1.0`，重复副作用 `0` | 冻结任务集的终态、幂等和安全契约 | 开放世界成功率 |
| 客服 HTTP v13 | 答案 `59/60`，引用支持 `20/34`，转人工 `59/60`，unsafe `0/60`，联合 `46/60` | 有人工兜底的受控客服回放 | 无人值守 grounded 客服、CSAT/FCR |
| 客服 HTTP v14 live observation | HTTP `60/60`；intent Macro-F1 `1.000000`；高风险召回 `1.000000`；答案人工质量指标待审 | 新 source fingerprint 下的本地 full-stack 执行与机器诊断 | 答案正确率、引用支持、线上 SLO；两个行为契约 badcase 仍保留 |
| 客服 HTTP v20 + 人工仲裁 | HTTP `60/60`；答案 `57/60`；引用 `25/36`；unsafe `1/60`；联合 `49/60` | 历史冻结输出的双盲+第三人仲裁回归证据 | 严格因果提升、无人值守客服、CSAT/FCR、生产 SLO |
| 客服 HTTP v27 + 人工仲裁 | 冻结答案 `59/60`；引用 `35/36`；联合 `59/60`；unsafe `0/60` | 历史 60 条人工基线 | 线上准确率、CSAT/FCR、严格因果提升、生产 SLO |
| 客服 HTTP v43 + 人工仲裁 | 答案 `107/120`；引用 `66/70`；联合 `105/120`；unsafe `1/120` | 120 条修复前基线；15 badcase | unseen/release/线上结论 |
| 客服 HTTP v54 + 人工仲裁 | 答案 `116/120`；引用 `63/67`；联合 `113/120`；unsafe `1/120` | 已见集修复中间点；7 badcase | unseen/release/线上结论 |
| 客服 HTTP v56 + 人工仲裁 | 答案 `120/120`；引用 `67/67`；联合 `120/120`；unsafe `0/120` | 当前已见集人工主证据；badcase 0 | unseen/release/线上结论、绝对安全 |
| 本地容量 v5 | c8 `1.353 QPS`，混合 P95 `10.574s`，LLM 路径 P95 `12.013s` | 本机短时诊断和长尾定位 | 生产持续吞吐或 SLO |
| DB batch/N+1 | 100 候选 batch offer/decision P50 `23.864/2.405ms`；N+1 `89.805/70.501ms` | 受控数据库批量化证据 | 线上容量保证 |

历史结果和坏例的完整口径见 [AI质量评测与Badcase](../evaluation/AI质量评测与Badcase.md)；v13、v14、v20、v27、v43、v54 均保留为各自只读 evidence，v56 是当前已见集人工答案入口，旧标签没有迁移到新输出。

## 验证与复现

```bash
cd AI_Shop-backend/AI_Shop-agent
conda run --no-capture-output -n shop python -m pytest -q
conda run --no-capture-output -n shop ruff check app evaluation tests scripts/project_closure_check.py
conda run --no-capture-output -n shop python -m compileall -q app evaluation scripts
conda run --no-capture-output -n shop python -m evaluation.cli validate
conda run --no-capture-output -n shop python -m evaluation.cli preflight --split regression

cd ..
mvn -pl AI_Shop-order/app -am test
```

当前源码 contract 证据：[report.md](closure-evidence/order-facts-contract-v1-20260825-r4/report.md)、[report.json](closure-evidence/order-facts-contract-v1-20260825-r4/report.json)、[evidence-manifest.json](closure-evidence/order-facts-contract-v1-20260825-r4/evidence-manifest.json)、[SHA256SUMS](closure-evidence/order-facts-contract-v1-20260825-r4/SHA256SUMS)。v14/v20/v27 live observation 和 preflight 仍作为历史阶段保留；当前人工答案以 [v56 final report](../../AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/customer-service/customer-service-http-v56-v3-knowledge-answer-review-human-approved-ai-assisted-20260827/final-report.md) 为准，源执行见 [v56 execution report](../../AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/customer-service/customer-service-http-v56-full-v3-knowledge-regressions-pending-human-review-20260827/report.md)。A/B 表、仲裁表、原始回传归档、父包和最终包均由 SHA-256 绑定。旧的 [preflight-regression-20260825.md](closure-evidence/preflight-regression-20260825.md) 仍是历史 fail-closed 快照。

## 仍然存在的限制

- 没有真实线上用户、曝光、点击、购买、CSAT/FCR 或生产流量；不新增 CTR/CVR/GMV、线上客服成功率或生产容量结论。
- `evaluation-evidence` 中各轮数字是 immutable archive；v13/v20/v27/v43/v54/v56 是不同冻结输出，不能写成严格因果提升。v56 的 `120/120` 只代表已见开发集，且零 badcase 的统计区间上界仍非零。
- v27 的 `cs-gold-v1-001`、v20 的 `014/018/043` 和 v43/v54 的全部 badcase 继续作为历史回归素材；不能因 v56 已关闭而删除失败证据。
- Search v4 已在同一 10 条已知 hard-negative 上改善，但尚无独立新 holdout，不能外推泛化质量。
- 本地延迟受共享机器、Provider 波动和 warm-up 影响；usage 缺失或未定价时费用保持 `null`，不能写成零成本。
- Java action capability 是写入前的只读预检，不是锁或事务保留；执行阶段仍需重新鉴权、校验状态并处理未知结果。
- v2.1 的标签决策已经人工审批，但来源独立复核 slot exact 仅 `0.50`；当前 final 也缺少仓库外生成与保管链证明，因此 `releaseGateEligible=false`。

## 最高优先级收口顺序

1. **已完成：** v56 全量 HTTP replay、A/B 人工审批、2 条第三人仲裁、不可变最终包与原始回传归档；所有历史包保持只读。
2. 由仓库外独立人员生成并保管新的 unseen holdout，预注册后一次性执行，再做双人独立复核和逐 case 仲裁；不得用当前源码或历史 badcase 反向构造答案。
3. 对 60 条来源样本完成独立全量审计和 custody attestation；把关键 slot 升级为 typed/hash-bound 字段，避免仅靠自由文本重叠判定。
4. 用新预注册 Search holdout 复验 query decomposition 与对象保留；固定 qrel 和分母，不继续重刷当前 10 条已知难例。
5. 只有进入真实试运行时再补持续容量、故障注入、成本和业务指标；当前本地 P50/P95/P99 继续只作诊断。
