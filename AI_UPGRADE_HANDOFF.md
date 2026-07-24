# AI Shop AI 能力改造 Handoff

更新时间：2026-07-24
当前分支：`dev`
远端仓库：`LittleSongxx/AI_Shop.git`
Git 身份：`song <2212565023@qq.com>`

本文用于交接当前 AI 电商客服、知识库和智能导购改造。项目已统一为一套主实现，后续应继续在现有 Agent、RAG、RabbitMQ Worker 和导购链路上演进，不要并行创建第二套版本。

## 一、当前完成度

当前版本已补齐课程/作品级真实 AI 电商系统的主要短板，覆盖客服意图与情绪处理、人工接管、FAQ 与 badcase 闭环、知识库运营、RAG 来源追踪、导购画像和实时商品过滤、队列优先级、熔断降级、基础观测与轻量验证。

仍未达到真实生产上线标准，主要差距已经从“核心功能缺失”转为“真实环境联调、效果数据积累和运营深化”。

## 二、已完成能力

### 1. AI 客服与人工接管

- 扩展投诉、主动转人工、支付、错发/破损、发票、地址修改、退款进度等客服意图。
- 识别负面情绪、紧急度和风险等级，并生成下一步动作与转人工原因。
- 用户明确要求人工、高风险或强负面场景可触发人工会话。
- 人工会话建立后，后续消息不再进入 AI 队列。
- 已支持人工认领、接入、回复、解决和转回 AI。
- C 端支持主动转人工和 AI 回复点赞/点踩。
- 管理端支持人工队列、会话历史、回复、badcase 处理和 FAQ 候选转化。

### 2. FAQ、badcase 与知识运营

- 正向反馈经过保守规则筛选后进入 FAQ 候选池。
- 负向反馈生成 `ai_badcase_candidate`，支持修正、忽略和转 FAQ。
- 已实现 TXT、Markdown、PDF、DOCX 文档解析。
- 入库链路包含文本规范化、标题识别、约 1200 字符切片、120 字符重叠和摘要 hash 去重。
- 已有文档、切片、入库任务、FAQ 候选和知识发布版本管理。
- 管理端知识工作台支持 FAQ 编辑、分类、渠道、语言、优先级、生效时间、文档发布/归档和候选审核。
- 发布后通过 Redis 知识版本广播使缓存失效。

### 3. RAG 检索、来源追踪与评测

- 检索链路为 ES 关键词检索、向量检索、RRF 合并和 rerank。
- 精确 FAQ 支持缓存、预热、版本隔离和 1.5 秒快路径超时。
- 精确 FAQ 命中时不调用 LLM，知识服务异常时可继续走普通客服链路。
- 普通混合 RAG 已返回结构化 `source_refs`，包含来源类型、文档/切片/FAQ ID、检索方式、分数、版本和摘要。
- Agent 状态已记录 `rag_trace` 和 `rag_source_refs`，普通 RAG 回复会把 trace 与 sources 写入消息 `source_refs`。
- trace 包含查询 hash、检索模式、命中状态、知识版本、来源数、最高分和延迟。
- 新增离线评测模块与命令，输出：
  - Recall@K；
  - MRR；
  - Top-K Hit Rate；
  - Answer Citation Rate。
- `scripts/rag_golden.jsonl` 是最小示例集，真实评测前必须替换为当前知识版本的真实 ID。

### 4. 智能导购画像与需求澄清

- 新增用户隔离的导购画像，Redis 保存 7 天。
- 规则抽取并跨轮次继承：
  - 品类；
  - 预算上下限；
  - 偏好品牌；
  - 排除品牌；
  - 使用场景；
  - 核心特征；
  - 是否接受替代品牌。
- “推荐点商品”等无约束请求会先追问品类、预算或场景。
- 已有明确画像时，模糊追问可复用历史约束。
- 用户表达“其他品牌也可以”后，偏好品牌变为软约束；排除品牌仍是硬约束。
- Redis 异常时画像读写会降级，不会让普通对话失败。

### 5. 商品约束、实时库存与推荐解释

- 商品召回后执行预算、品牌、排除品牌、在售状态和明确零库存过滤。
- 约束不满足时返回 `constraint_miss`，不会再偷偷用无关热销商品覆盖结果。
- 所有匹配商品售罄时返回 `out_of_stock`，给出明确库存提示。
- 商品快照和 Agent 搜索卡已补充：
  - 最低价和最高价；
  - 品牌；
  - 属性与 SKU；
  - 商品总库存与是否有货；
  - 推荐理由。
- 库存服务增加批量商品库存聚合接口，避免逐商品 N+1 查询。
- 商品服务一次批量获取库存；库存服务不可用时返回“库存未知”，保留搜索能力，不误判为售罄。
- 推荐理由基于实际画像和检索结果生成，例如“符合预算”“匹配品牌偏好”“适合办公”。
- C 端商品卡展示价格区间、品牌、库存状态和简短推荐理由。

### 6. 队列、熔断与流量健壮性

- Agent 任务使用 RabbitMQ 持久化队列：
  - `agent.support.high`；
  - `agent.faq.fast`；
  - `agent.shopping.low`；
  - `agent.tasks.dead`。
- 投诉、支付、退款和人工问题进入高优先级；FAQ 进入快队列；普通导购进入低优先级。
- Worker 支持用户级 Redis 锁、任务状态、发布确认、重试、deadline、死信、恢复、心跳和健康检查。
- 恢复扫描只重新投递 `PENDING/FAILED`，不会重复发布 RabbitMQ 中仍在排队的任务。
- LLM 支持主模型熔断和备用模型切换。
- 新增队列优先级、deadline 和熔断策略测试。
- 新增异步轻量压测脚本，可统计状态码、平均延迟、P95 和最大延迟。

### 7. 观测与客服 SLA

- 已有 LLM、RAG、熔断、工具调用、Worker、死信、锁竞争和任务积压等 Prometheus 指标。
- 新增人工客服 SLA 统计：
  - 会话总量和状态分布；
  - 活跃会话数；
  - 平均排队等待；
  - 平均首次响应；
  - 平均解决时长；
  - 首次响应 SLA 达标率；
  - 排队超时数；
  - 已接入但首次响应超时数；
  - 各客服当前活跃会话数。
- 默认首次响应 SLA 为 300 秒，排队告警为 600 秒，可通过 Agent 配置修改。
- 管理端人工会话页已展示 SLA 摘要。

## 三、本次验证结果

2026-07-24 已执行并通过：

```text
Simlect-backend/Simlect-agent:
  ./.venv/bin/ruff check app tests scripts
  ./.venv/bin/pytest -q
  123 passed, 1 dependency warning

Simlect-backend:
  mvn -q -pl Simlect-stock,Simlect-product,Simlect-search,Simlect-admin \
      -am test -DskipTests=false
  passed

Simlect-front/Simlect-web:
  npm run build
  passed

Simlect-front/Simlect-admin:
  npm run build
  passed

Repository:
  git diff --check
  passed
```

已知警告均为既有依赖或构建提示：

- LangGraph 依赖的未来弃用提示；
- Maven/Mockito 的 JDK 动态 agent 提示；
- Vite 的 `:deep`、第三方 pure annotation 和 chunk 体积提示。

本轮没有启动完整中间件和 LLM，因此未执行真实 Redis、MySQL、RabbitMQ、ES、Nacos、Embedding、Rerank、LLM 联调，也未执行带有效 Token 的在线压测。

## 四、后续任务

### P1：导购体验深化

- 增加同类商品结构化对比，展示价格、核心属性、销量和库存差异。
- 增加简单多样性重排，避免候选全部来自同一品牌或同一价位。
- 对规则词典之外的品牌、品类和复杂约束增加低风险的 LLM 补充抽取，但不要让未校验的 LLM 结果直接成为硬过滤条件。
- 增加低置信度澄清，不只处理完全无约束的泛化请求。
- 记录 Agent 推荐曝光、点击、进入详情、加购和成交事件，形成基础归因漏斗。

### P1：RAG 真实质量闭环

- 用当前数据库真实 FAQ、文档和 chunk ID 扩充黄金集。
- 覆盖相似问法、过期政策、召回为空、多文档冲突、隐私问题、prompt injection、商品事实和库存事实。
- 在完整环境运行 `scripts/eval_rag.py`，将基线指标记录到文档或 CI 产物。
- 管理端可进一步展示普通 RAG 的来源、版本和分数，便于人工复盘。
- 根据线上 badcase 调整切片、召回权重和 rerank，而不是只修改最终提示词。

### P1：客服运营深化

- 按售后、支付、物流等技能做简单路由。
- 人工会话结束后增加满意度采集。
- 转回 AI 前生成受控上下文摘要。
- badcase 按意图、情绪、知识版本、模型版本和来源聚合。
- 增加客服并发上限或忙碌状态，避免单个客服认领过多会话。

### P1：真实环境韧性验证

- 启动全套中间件后运行轻量压测脚本，验证高优先级队列在积压时是否优先。
- 分别模拟 LLM、ES、Embedding、Rerank、Redis、RabbitMQ 和 Java 内部服务超时。
- 验证 Worker 重启、RabbitMQ 重连、重复投递、死信恢复和消息幂等。
- 验证 FAQ 预热后在 LLM 不可用时仍能提供快问快答。
- 根据真实结果调整队列上限、超时、熔断阈值和 Worker 并发。

### P2：安全与数据治理

- 复核管理端知识库接口的 Gateway 管理员鉴权。
- 检查上传类型、文件大小和恶意文档内容。
- 对文档、日志和 `source_refs` 中的手机号、邮箱、订单号等信息做脱敏审计。
- 增加文档 prompt injection 测试和交易规则来源优先级约束。
- 确保退款、支付、物流、价格和库存事实始终以 Java 业务接口为准。

## 五、运行辅助命令

RAG 在线评测：

```bash
cd Simlect-backend/Simlect-agent
./.venv/bin/python scripts/eval_rag.py \
  --dataset scripts/rag_golden.jsonl \
  --top-k 5
```

Agent API 轻量压测：

```bash
cd Simlect-backend/Simlect-agent
./.venv/bin/python scripts/smoke_load.py \
  --token "<development-user-token>" \
  --requests 100 \
  --concurrency 10
```

常规回归：

```bash
cd Simlect-backend/Simlect-agent
./.venv/bin/ruff check app tests scripts
./.venv/bin/pytest -q

cd ../
mvn -q -pl Simlect-stock,Simlect-product,Simlect-search,Simlect-admin \
  -am test -DskipTests=false
```

## 六、关键文件

### Agent

- `Simlect-backend/Simlect-agent/app/services/shopping_profile_service.py`
- `Simlect-backend/Simlect-agent/app/services/product_service.py`
- `Simlect-backend/Simlect-agent/app/services/support_service.py`
- `Simlect-backend/Simlect-agent/app/rag/retriever.py`
- `Simlect-backend/Simlect-agent/app/rag/evaluation.py`
- `Simlect-backend/Simlect-agent/app/graph/nodes.py`
- `Simlect-backend/Simlect-agent/app/services/agent_queue_service.py`
- `Simlect-backend/Simlect-agent/app/worker.py`
- `Simlect-backend/Simlect-agent/scripts/eval_rag.py`
- `Simlect-backend/Simlect-agent/scripts/smoke_load.py`

### Java

- `Simlect-backend/Simlect-product/app/src/main/java/com/simlect/biz/ProductInternalService.java`
- `Simlect-backend/Simlect-product/app/src/main/java/com/simlect/controller/internal/ProductAgentInternalController.java`
- `Simlect-backend/Simlect-stock/app/src/main/java/com/simlect/biz/SkuStockService.java`
- `Simlect-backend/Simlect-stock/api/src/main/java/com/simlect/api/support/StockFeignSupport.java`
- `Simlect-backend/Simlect-search/src/main/java/com/simlect/biz/impl/KnowledgeBaseServiceImpl.java`
- `Simlect-backend/Simlect-search/src/main/java/com/simlect/component/RabbitMQRagListenerComponent.java`

### 前端

- `Simlect-front/Simlect-web/src/components/agent/AgentProductList.vue`
- `Simlect-front/Simlect-web/src/views/agent/AgentSendPanel.vue`
- `Simlect-front/Simlect-admin/src/views/setting/AgentMessageList.vue`
- `Simlect-front/Simlect-admin/src/views/setting/Rag.vue`

## 七、继续开发约束

- 不恢复旧 `RagQuestionController`，知识运营统一使用当前 RAG 控制器和工作台。
- 不重新引入进程内并发池作为主任务队列，RabbitMQ + Worker 是唯一主链路。
- 不创建第二套导购画像、RAG 或客服版本。
- 不把个性化商品推荐结果写入全局缓存。
- 不用 RAG 或 LLM 覆盖订单、支付、退款、价格和库存等实时业务事实。
- 数据库升级脚本仍需在目标环境执行，编译通过不代表目标环境已完成升级。
