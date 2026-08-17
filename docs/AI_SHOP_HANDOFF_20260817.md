# AI_Shop 双主线求职证据闭环实施 Handoff

> 交接日期：2026-08-17（Asia/Hong_Kong）
>
> 当前正式进度：**75%**
>
> 当前分支：`feat/multi-agent-harness`
>
> 实施起点：`6eb8e8eb822a20394e6cc05958d72823379614cc`
>
> 证据边界：`SYNTHETIC + local-live`，不能表述为生产效果或真实用户实验

## 1. 交接目标

项目已收敛为两条并列、可在面试中完整叙述的业务主线：

1. **可信 AI 推荐导购**：多轮需求澄清、Mission 建模、混合/视觉搜索、Java 权威价格库存校验、可解释排序与比较、曝光点击归因、交易结果回流、长期 Profile 影响下一次推荐。
2. **受控售后 Agent**：自然语言定位订单、RAG 政策证据、Java 权威事实、结构化提案、用户确认、Java 鉴权/归属/状态/幂等校验、未知结果转人工复核、Episode/评测/脱敏 Trace。

下一位接手者的目标不是继续扩功能，而是完成真实 Provider 下的正式证据采集：

- Search v3 known + 一次性 fresh/challenge/runtime holdout。
- RAG v5 context index、known、一次性 retrieval/generation fresh。
- Agent v2 44 条 adaptive live、三种固定编排模式消融和两条正式退款 Trace。
- 两名不同真人完成 RAG v5 fresh 20 条盲评。
- 更新证据清单和最终面试口径。

未完成真人盲评前，实施进度最多只能写 **90%**。不能使用模型冒充真人 Reviewer。

## 2. 已完成的代码闭环

### 2.1 可信 AI 推荐导购

- 新增预算/步数/成本守卫和编排策略，保留可审计的模型、工具和终态事实。
- Mission 的明确预算、品类、品牌和功能约束优先于长期 Profile；Profile 只能做小幅排序调整，不能违反当前硬约束。
- 推荐结果携带报价快照、请求 ID、位置和检索模式，支持比较前刷新权威报价。
- 点击必须绑定此前真实曝光，伪造商品点击不会写入推荐事件。
- 支付路径写入幂等 `PAYMENT`，同用户同商品再次成功购买写入幂等 `REPEAT_PURCHASE`。
- 退款、低分评价、售后联系形成负反馈；隐式信号继续使用 180 天线性衰减。
- `SUPPORT_CONTACT` 强度为 `0.35` 的 `negativeProduct` 弱信号，不能压过当前 Mission。
- Agent v2 冻结契约共 44 条：37 条继承单轮任务，新增 7 条多步导购/视觉/归因/交易/反馈 sequence。

### 2.2 受控售后 Agent

- `support_session` 新增可空 `context_json`，迁移与 Alembic/Flyway 入口已覆盖。
- 结构固定包含 schema version、脱敏诉求、有限近期对话、分诊信息、转人工原因、未验证线索和 Java 权威订单事实。
- 最近对话最多 6 条，每条最多 200 字，总上下文有硬上限。
- 模型抽取订单号只进入 `unverifiedHints`；只有 Java 内部订单接口返回且通过当前用户归属校验的事实才能标记：
  - `authority=JAVA_ORDER_SERVICE`
  - `ownershipVerified=true`
- 跨用户、服务异常或歧义时不泄露订单事实。
- 用户侧 public session 契约保持不变；管理端仅在 `support:read`/`audit:read` 下展示 `handoffContext`。
- 管理端抽屉分区展示诉求、分诊、近期对话、权威订单事实；列表继续兼容原 `summary`。
- 隐私导出升级为 `aishop-user-ai-export/v2` 并包含结构化上下文；彻底删除沿用 message/case/session 顺序并清除 `context_json`。
- 售后提案必须经过用户确认；Java 继续执行鉴权、归属、状态和幂等校验；远端未知结果保持 `INCONCLUSIVE/MANUAL_REVIEW`。

### 2.3 知识发布与历史可复现性

- 新增不可变知识发布快照，版本单调递增；回滚通过“旧快照重新激活为更高新版本”完成，禁止版本倒退。
- 管理端新增 `POST /admin/knowledge/activateRelease`。
- 内部 `POST /internal/search/knowledge/catalog` 支持可选 `releaseVersion`；运行时读当前版本，评测可固定历史版本。
- 发布、归档、重建和并发激活都锁定全局版本行。
- 保留 `demo_knowledge` v1 及其全部历史 SHA；新增独立 `demo_knowledge_v2`。
- v2 新事实仅包含已核实变化：
  - 签到中断后当天连续天数从 1 重新累计，补签后向前重算连续段。
  - 转人工携带脱敏摘要、有限对话、分诊信息和归属校验后的订单上下文。
- RAG v4 工具继续固定 catalog v1；RAG v5 固定 catalog v2 和不可变 release。

## 3. 已完成的自动验证

以下结果在本轮代码上已经实际执行，不是计划值：

| 验证项 | 结果 |
| --- | --- |
| Python 全量 pytest | `1162 passed, 7 skipped` |
| MySQL 8.4.11 容器补跑 7 个迁移场景 | `8 passed`（参数化后共 8） |
| Ruff | 通过 |
| Java `mvn test` | 26 模块 `BUILD SUCCESS` |
| Java `mvn verify -Pintegration` | 26 模块 `BUILD SUCCESS` |
| `MiddlewareIT` | 7/7 |
| `TransactionPersistenceIT` | 3/3 |
| 管理端 Vitest/lint/build/budget | 10/10，全部通过 |
| 用户端 Vitest/lint/build/budget | 33/33，全部通过 |
| Search v3 / RAG v5 `prepare` | 通过，fresh 均为 `NOT_EXECUTED` |
| Agent v2 `--validate-only` | 44/44 契约通过 |
| Evidence manifest | 29 entries valid |
| Evidence validator tests | 7/7 |
| `git diff --check` | 通过 |

完整服务栈也曾在本地向量回退模式下实际启动成功：

- 10 个 Java 服务、Gateway、MCP、Agent API、Worker、两个前端和可观测栈均 ready。
- 商品 47、SKU 135、图片资产 487 校验通过。
- 本地向量索引当时包含商品 47、FAQ 6、知识切片 75。
- `demo-knowledge-v2-4be381333263` 激活为 release 21，精确包含 12/12 文档。
- 已创建两个仅供回退验证的隔离索引：
  - `aishop_eval_rag_original_v5_local_smoke`
  - `aishop_eval_rag_context_v5_local_smoke`
- 这两个索引使用 `local-hash-v1`，**禁止用于正式证据**。

## 4. 当前运行现场

### 4.1 进程状态

最后一次已完整执行 `./stop.sh`：

- 所有托管 Java、Agent、MCP 和前端进程均已停止。
- Docker 中间件保持运行：MySQL、Redis、RabbitMQ、Nacos、Elasticsearch、Sentinel、Seata 和可观测组件的数据未清理。
- `run/runtime.env` 仍存在，包含本地端口和私密运行配置；不要提交、打印或复制其中的值。

### 4.2 Elasticsearch 兼容性处理

首次启动发现旧 ES 数据卷由 Lucene major 10 写入，而当前镜像为 Elasticsearch 8.19.19 / Lucene 9，报错：

```text
indexCreatedVersionMajor is in the future: 10
```

已经完成可恢复处理：

- 旧卷完整备份为 `shop_aishop_es_backup_20260817_lucene10`。
- 源卷和备份均为 225 个文件。
- 两者内容聚合 SHA-256 均为：
  `2644c8f17fe2ad9f4f50dadeff0f9fe3e86cf668a0fc3cda8c1a2ed3d2a81d52`
- 当前 `shop_aishop_es` 是 Elasticsearch 8.19.19 新建的兼容卷。
- **禁止删除备份卷**，除非用户明确授权。

### 4.3 知识快照

release 21 的目录事实已经通过内部接口和本地文件双重校验：

```text
releaseName: demo-knowledge-v2-4be381333263
documents: 12/12
catalog SHA-256: 4be3813332639e7c78b55e3f02396cb4a50e24cf99de816ce07972bc9c77cb9e
fact metadata SHA-256: 5cc105b36a811a0d97e901c0098b7eb717132f873b256a680275dc805db1b641
```

注意：release 21 是在本地向量契约下完成的最后一次现场验证。切回真实 Embedding 后，知识向量重建可能推进全局 release；正式 RAG 不要盲用 21，必须重新读取并固定新 release。

## 5. Provider 现场与阻塞

私密配置位于 `AI_Shop-backend/AI_Shop-agent/.env`，该文件被忽略，不能提交或在日志中输出值。

已做最小真实调用探针：

| 能力 | 当前结果 | 结论 |
| --- | --- | --- |
| DeepSeek LLM | HTTP 200，返回 1 个 choice | 可用 |
| Qwen Embedding | HTTP 200，返回 1 个 1024 维向量 | 新 Key/Base URL 可用 |
| Qwen Rerank（当前 `RERANK_API_KEY`） | HTTP 401 `invalid_api_key` | 文件里仍是旧 Key |
| Qwen Rerank（临时使用新 `DASHSCOPE_API_KEY`） | HTTP 200，返回 2 条结果 | 端点和新 Key 可用 |
| VLM grounding（新 `DASHSCOPE_API_KEY`） | HTTP 403 `insufficient_quota` | 鉴权成功但无 VLM 配额 |

当前 `.env` 的关键事实：

- `EMBEDDING_API_KEY` 已更新且可用。
- `DASHSCOPE_API_KEY` 已更新且可用于当前 Rerank 端点。
- `RERANK_API_KEY` 尚未同步，仍会 401。
- `VLM_API_KEY` 仍为空；当前视觉链路回退到其他别名 Key，但模型调用返回配额不足。

正式启动前必须做到：

1. 在不打印值的前提下，让 `RERANK_API_KEY` 使用已验证的新 Qwen/DashScope Key。
2. 配置一个有 `qwen3-vl-plus`、视觉 embedding 和视觉 rerank 权限/配额的 Key；若是独立 Key，写入 `VLM_API_KEY`/视觉专用配置。
3. 对 Embedding、Rerank、VLM grounding、视觉 embedding、视觉 rerank 分别做一次最小探针。
4. 只有真实调用均成功，才运行 Agent v2 正式 live。

## 6. Fresh 数据锁状态

截至交接时，以下三个一次性锁均不存在：

```text
AI_Shop-backend/AI_Shop-agent/benchmarks/results/search-v3/_fresh-execution-lock.json
AI_Shop-backend/AI_Shop-agent/benchmarks/results/rag-v5/_retrieval-fresh-execution-lock.json
AI_Shop-backend/AI_Shop-agent/benchmarks/results/rag-v5/_generation-fresh-execution-lock.json
```

规则：

- known 回归、服务 readiness、知识 release 和 Provider 完整性全部通过之前，禁止打开 fresh。
- fresh 一旦执行，无论通过还是失败都永久保留执行锁和原始结果。
- fresh 失败后不得调参再把同一数据称为 fresh；该集合转为下一版 known，下一版必须新建并锁定未见集。
- 禁止使用 `--accept-baseline` 消除失败，禁止覆盖历史 `FAILED_RETAINED`。

## 7. 下一位 AI 的执行顺序

### 7.1 恢复真实 Provider 完整栈

1. 确认私密 `.env` 中 Rerank/VLM 配置已正确更新，不输出密钥。
2. 从仓库根目录运行 `./start.sh`。不要再设置 `SPRING_AI_MODEL_EMBEDDING=local`。
3. 启动脚本应识别当前 `local-hash-v1` 向量契约与 `text-embedding-v4` 不一致，并在开发模式下重建商品、FAQ 和知识向量。
4. 确认最终 `/health/dependencies` 至少满足：
   - `llm=true`
   - `embeddingProvider=openai`
   - `embeddingProductionReady=true`
   - `rerank=true`
   - `javaGateway=true`
   - `mcp=true`
5. 不能只相信布尔配置状态；仍要使用最小真实调用或正式 Runner provider facts 验证 Provider 请求成功。

### 7.2 重新发布并冻结知识 v2

从仓库根目录运行：

```bash
/home/song/anaconda3/envs/shop/bin/python scripts/bootstrap_demo.py
```

读取新激活的 release version，并验证：

```text
catalog SHA = 4be3813332639e7c78b55e3f02396cb4a50e24cf99de816ce07972bc9c77cb9e
fact metadata SHA = 5cc105b36a811a0d97e901c0098b7eb717132f873b256a680275dc805db1b641
documents = 12/12
```

后续所有 RAG v5 阶段使用同一个不可变 release version。

### 7.3 Search v3

正式 Run ID 使用推送后的实际短 SHA，不再使用实施起点 `6eb8e8e`：

```bash
cd AI_Shop-backend/AI_Shop-agent
/home/song/anaconda3/envs/shop/bin/python benchmarks/run_search_v3_eval.py prepare
/home/song/anaconda3/envs/shop/bin/python benchmarks/run_search_v3_eval.py collect-known \
  --run-id search-v3-<short-sha>-20260817
```

known 可以修复，但不能接触 fresh。只有 known 和 Provider completeness 通过后才运行：

```bash
/home/song/anaconda3/envs/shop/bin/python benchmarks/run_search_v3_eval.py collect-final \
  --run-id search-v3-<short-sha>-20260817 --finalize-holdout
/home/song/anaconda3/envs/shop/bin/python benchmarks/run_search_v3_eval.py package \
  --run-id search-v3-<short-sha>-20260817
```

正式门禁：

- fresh Recall@3 `>=0.85`
- fresh NDCG@5 `>=0.80`
- challenge 正例 Recall@3 `>=0.85`
- challenge 无结果准确率 `>=0.90`
- ProductService Recall@10 `>=0.80`、MRR `>=0.65`、NDCG `>=0.70`
- 约束违规 `0`
- Provider 完整率 `100%`

### 7.4 RAG v5

不要复用带 `_local_smoke` 后缀的索引。使用真实 Provider 创建正式隔离索引：

```bash
/home/song/anaconda3/envs/shop/bin/python benchmarks/run_rag_v5_eval.py prepare
/home/song/anaconda3/envs/shop/bin/python benchmarks/run_rag_v5_eval.py prepare-context \
  --source-index aishop_vectorstore
/home/song/anaconda3/envs/shop/bin/python benchmarks/run_rag_v5_eval.py collect-known \
  --run-id rag-v5-<short-sha>-20260817 --release-version <release-version-v2>
```

known 和 Provider completeness 通过后，才运行一次性检索 fresh：

```bash
/home/song/anaconda3/envs/shop/bin/python benchmarks/run_rag_v5_eval.py collect-final \
  --run-id rag-v5-<short-sha>-20260817 --release-version <release-version-v2> \
  --finalize-holdout
/home/song/anaconda3/envs/shop/bin/python benchmarks/run_rag_v5_eval.py package \
  --run-id rag-v5-<short-sha>-20260817
```

检索通过后再执行 generation known/fresh/package。fresh 生成完成后只产生盲评材料，状态保持 `HUMAN_REVIEW_PENDING`。

### 7.5 Agent v2 与 fixture

当前尚不存在：

```text
AI_Shop-backend/AI_Shop-agent/benchmarks/task_success_v2_bindings.local.json
```

以 `task_success_v2_bindings.example.json` 为字段模板，在隔离评测环境绑定 44 条任务所需的真实 token、user、order、order item、SKU、地址和已审核图片资产。该文件被忽略，禁止提交。

必须满足：

- `AISHOP_EVAL_ISOLATED=enabled`
- `FAULT_PROFILE_VISUAL_PROVIDER_UNAVAILABLE=enabled`
- `FAULT_PROFILE_UNKNOWN_OUTCOME=enabled`
- 每个模式运行前恢复同一业务 fixture snapshot。
- API 和 Worker 使用同一个 `ORCHESTRATION_MODE`。
- 内部 token 只从环境或私密 CLI 输入读取，不能进入 bindings 或报告。

先运行 adaptive 44 条，再分别恢复同一 fixture 运行 workflow、single-agent、multi-agent，最后执行配对消融比较。任何模式若不是 100% 执行和 Provider 完整，都不能生成有效配对结论。

### 7.6 正式 Trace 和真人盲评

从真实 Episode 导出：

1. 用户确认且最终为 `CONFIRMED` 的退款。
2. 远端结果未知且进入 `INCONCLUSIVE/MANUAL_REVIEW` 的退款。

使用 `scripts/export_interview_traces.py`，检查 manifest、SHA256SUMS 和脱敏结果。不得把 action token、凭据、用户标识或业务标识写入证据。

RAG v5 fresh 20 条需要两名不同真人、不同稳定 Reviewer ID，分别标注：

- grounded
- complete
- citationAligned
- safe

两人都提交前不能到 100%。合并时报告 Cohen's kappa 和全部分歧；安全维度必须 20/20。

## 8. 历史失败与正确口径

历史失败必须保留，它们是“发现问题、修复、重新设计门禁”的面试证据：

- Search v2 challenge 无结果准确率为 `0.80`。
- 首次 ProductService `Recall@10=0.3778`。
- RAG v4 fresh 检索指标不足。
- RAG v4 generation 只完成 `39/60`。
- “签到中断规则”和“转人工上下文”两个标签超出当时已发布知识。

Provider 调用完整、证据格式正常；`FAILED_RETAINED` 是真实质量失败，不是基础设施故障。v2 知识和 v5 契约用于验证修复，不能篡改旧结果或降低阈值。

## 9. Git 与提交边界

本轮要求把工作区全部项目改动按内容分批提交并推送当前分支。私密/运行时文件仍必须保持未跟踪：

- `AI_Shop-backend/AI_Shop-agent/.env`
- `run/runtime.env`
- `benchmarks/task_success_v2_bindings.local.json`
- `benchmarks/results/**` 中的本地运行产物

已完成的分批提交：

1. `607aa25` `feat(agent): close governed shopping and support loops`
   - Agent 编排、预算、受控售后上下文、隐私和推荐反馈闭环。
2. `9bcd866` `feat(java): version knowledge releases and commerce outcomes`
   - Java 知识快照、订单复购反馈、数据库契约和权威边界测试。
3. `07774e6` `feat(frontend): expose governed support handoff context`
   - 管理端结构化 handoff 展示、测试和前端资产更新。
4. `5047a7f` `feat(eval): add versioned search rag and agent live gates`
   - Search v3、RAG v5、Agent v2、消融、盲评、Trace 和冻结数据契约。
5. `25895b1` `feat(knowledge): publish versioned support policy catalog`
   - demo knowledge v2、canonical fact metadata、bootstrap release 激活及测试。

README、证据清单、求职材料和本 handoff 位于紧随上述提交的文档批次中。接手时以远端 `feat/multi-agent-harness` 的最新提交为准。

交接者应先运行 `git log --oneline --decorate -10` 确认最终推送提交，再开始正式评测；Run ID 必须使用推送后实际代码 SHA。

## 10. 进度更新规则

- 当前保持 **75%**：代码、迁移、自动测试、数据锁和本地完整栈已通过。
- 真实 Provider 完整栈、Search/RAG/Agent 正式 live、消融和 Trace 全部完成后，最多更新到 **90%**。
- 两名真人 Reviewer 完成并合并后，才可更新到 **100%**。
- 任何正式未见集失败，进度保持在对应阶段，保留 `FAILED_RETAINED`，不得用失败结果冒充通过。
