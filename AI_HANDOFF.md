# AI_Shop AI Handoff

> 仅供接手本任务的 AI 阅读。不要把本文原样贴给用户；面向用户时应重新组织为结论、证据和风险。
>
> 交接日期：2026-08-16（Asia/Shanghai）
>
> 仓库：`https://github.com/LittleSongxx/AI_Shop.git`
>
> 分支：`feat/multi-agent-harness`

## 0. 接手后第一件事

1. 克隆或进入仓库，切到 `feat/multi-agent-harness`，执行：

   ```bash
   git fetch origin
   git switch feat/multi-agent-harness
   git pull --ff-only
   git status --short --branch
   git rev-parse HEAD
   ```

2. 本文随本轮汇总提交一起推送。正常情况下，新机器检出后工作区应干净。若工作区不干净，先阅读差异，绝不能直接重置或覆盖用户改动。
3. 不要寻找或依赖名为 `shop` 的 Docker 容器。本机实测不存在该容器；`shop` 是 Python Conda 环境名。新机器可新建同名 Conda 环境，也可通过 `AISHOP_PYTHON=/absolute/path/to/python` 使用普通 venv。
4. `run/`、`.env`、日志、PID、本地数据库备份和生成的凭据均被 Git 忽略，不会随提交迁移。新机器必须重新生成本地运行状态和秘密。

## 1. 用户目标与任务背景

用户最初要求：

- 全面分析 AI_Shop 项目，评估技术栈和基础设施选型。
- 重点判断 RabbitMQ 是否应该替换为 RocketMQ。
- 确认项目是否使用 pgvector 或专门向量数据库；用户观察到当前似乎由 Elasticsearch 存储向量。
- 结合联网搜索，按“电商 + AI 客服导购”领域适配性给出针对性结论。

之后用户把目标扩展为：

- 先实施本次发现的全部问题和优化项，再继续深挖潜在问题。
- 在本地部署完整项目并验证正确性。
- 检查本地所谓 `shop` Docker 容器是否符合项目需求。
- 执行过程中持续给出阶段百分比。

最新指令是：

- 把全部背景、进度、证据和后续计划写成可跨电脑接手的 handoff。
- 检查换机所需环境依赖。
- 提交并推送全部代码。

本文件完成最新指令中的知识交接。代码、测试和部署工作已经大体完成；联网技术选型报告与第二轮潜在问题审计尚需接手 AI 收尾。

## 2. 仓库状态基线

- 本轮开始前 HEAD 与远端均为 `91ffbbf`：
  `feat: add Search v2 and RAG v4 evaluation evidence`。
- 本轮在该基线后积累了约 192 个已跟踪文件的修改，统计约为
  `6155 insertions(+), 1815 deletions(-)`，并新增多批源码、测试、部署脚本与配置。
- 汇总提交会包含本文件及当时工作区全部应提交内容。
- `docs/技术选型评审.html` 当前被删除；不要在没有上下文的情况下恢复它。正式技术选型结论应优先形成新的可维护 Markdown 报告，或确认现有文档策略后再决定是否补回 HTML。
- 两份中文面试资料是工作区中新增的项目资料，也已按“提交全部代码/内容”的用户指令纳入提交。

## 3. 当前系统结构

### Java 微服务

- Spring Boot 3 / Spring Cloud / Spring Cloud Alibaba
- Gateway、User、Product、Stock、Cart、Order、Pay、Coupon、Search、Admin
- MyBatis、Flyway、OpenFeign
- Nacos 注册与配置
- Seata AT 分布式事务
- Sentinel 限流熔断
- RabbitMQ 领域事件与异步任务
- Redis 缓存、分布式协调、幂等辅助
- MySQL 业务持久化
- Elasticsearch 商品搜索、知识检索和向量检索

根 `pom.xml` 当前声明：

- Java 编译目标 17
- Spring Cloud `2025.0.3`
- Spring Cloud Alibaba `2025.0.0.0`
- MySQL 驱动版本属性 `8.4.0`
- RabbitMQ Java client 版本属性 `5.22.0`

本机实际验证使用 JDK `21.0.11` 和 Maven `3.9.16`。

### Python Agent

- Python `>=3.11,<3.14`
- FastAPI `0.141.1`
- LangGraph `1.0.10`
- langchain-openai `1.1.14`
- MCP `1.28.1`
- SQLAlchemy / Alembic / aiomysql
- aio-pika / Redis
- OpenTelemetry / Prometheus / structlog
- pytest / pytest-asyncio / Ruff

本机实际验证使用 `/home/song/miniconda3/envs/shop/bin/python`，版本 `3.12.13`。

### 前端

- 商城：Vue 3、Vite 8、TypeScript 6、Element Plus、Vitest、Playwright、PWA。
- 管理端：Vue 3、Vite 7、Element Plus、Vitest。
- 管理端明确要求 Node `^20.19.0 || >=22.12.0`。
- 本机实际验证使用 Node `25.9.0`、npm `11.12.1`。

### Docker 中间件

`deploy/docker-compose.middleware.yml`：

- MySQL `8.4.11`
- Redis `7.4.7-alpine`
- RabbitMQ `4.2.9-management`
- Nacos `2.5.3`
- Elasticsearch `8.19.19` + IK 自建镜像
- Sentinel `1.8.8`
- Seata `2.5.0` 自建镜像

`deploy/docker-compose.observability.yml`：

- Tempo `2.10.8`
- OpenTelemetry Collector Contrib `0.158.0`
- Loki `3.7.5`
- Alloy `1.18.1`
- Prometheus `3.13.2`
- Grafana `13.1.3`

本机 Docker 为 `28.5.1`，Compose 为 `v2.40.3-desktop.1`，运行于 WSL2 Linux。

## 4. 本轮已完成的实现

### 4.1 RabbitMQ 与最终一致性

主要文件：

- `AI_Shop-backend/AI_Shop-common/src/main/java/com/aishop/constants/RabbitMQConfig.java`
- `TransactionalMqSender.java`
- `ReliableMessageSender.java`
- `MqPublisherConfirmHelper.java`
- `MqListenerHelper.java`
- `MqConsumerIdempotencyHelper.java`
- `OutboxMessageServiceImpl.java`
- `OutboxDispatchTask.java`
- `MqCompensationAutoReplayTask.java`
- `MqConsumeReplayRouter.java`
- `MqIdempotencyKeys.java`
- `deploy/rabbitmq/apply-policies.sh`
- `deploy/rabbitmq/rabbitmq.conf`

已实施：

- 生产者 Confirm 与 Return 检查，阻止“发布调用返回即视为成功”的错误语义。
- 本地事务 Outbox，事务提交后发送；发送失败由带租约的补偿扫描继续处理。
- Outbox 指数退避、最大重试、耗尽状态、人工查询与重放接口。
- 消费端显式 ack/nack、分级重试队列、死信路由、幂等键约束。
- 队列默认使用 quorum 类型。
- RabbitMQ policy 为关键 quorum 队列启用 at-least-once dead lettering 和
  `reject-publish` overflow。
- 修复订单、支付超时、退款、库存、用户成长、通知、RAG 等监听器中的重复消费和失败处理。
- 补充真实 RabbitMQ Publisher Confirm / unroutable return 集成测试。

注意：

- 当前是单 RabbitMQ 节点的本地部署。quorum queue 在单节点环境不能证明节点级高可用；生产至少需要奇数节点集群和跨故障域设计。
- 多数消费者 `prefetch=1`，偏保守可靠性。吞吐测试后可按消费者幂等性与处理时延单独调优。

### 4.2 数据库最小权限与迁移身份

主要文件：

- `start.sh`
- `deploy/provision-app-mysql-user.sh`
- `deploy/provision-flyway-identity.sh`
- `deploy/provision-infrastructure-mysql-users.sh`
- 各 Java 服务 `application.yml`
- Agent `app/db/pool.py`、`app/db/analytics_pool.py`
- `deploy/env.production.example`

已实施：

- root 只用于本地引导和 schema 管理。
- Java/Agent 业务运行使用 DML-only 的 `aishop` 身份。
- Flyway 与 Alembic 使用独立迁移身份。
- Nacos、Seata 使用各自 schema 级账号，不再共用 root。
- Analytics reader 保持只读边界。
- 数据库连接池改为受控小池，适合本地同时启动十个 JVM。
- 新增 Java/Python 生产配置安全校验，生产模式拒绝默认密码、认证绕过和不安全身份。
- MySQL 8.4 初始化、Nacos/Seata 元数据及 `undo_log` 已在真实容器验证。

### 4.3 Elasticsearch 向量与 RAG 契约

当前项目没有使用 pgvector。AI_Shop 的文本和视觉向量均存储在 Elasticsearch
`dense_vector` 字段中。默认：

- 向量索引 `aishop_vectorstore`
- 向量字段由 `VECTOR_FIELD` 配置
- 维度 1024
- 索引类型 `int8_hnsw`
- 相似度 `cosine`
- schema contract version 1

主要文件：

- Agent `app/rag/index_contract.py`
- Agent `app/rag/embedding.py`
- Agent `app/rag/local_embedding.py`
- Agent `app/rag/retriever.py`
- Search `VectorIndexContractHealthIndicator.java`
- Search `EmbeddingModelHealthIndicator.java`
- Search `LocalEmbeddingConfiguration.java`
- Search `LocalHashEmbeddingModel.java`
- Search `KnowledgeBaseServiceImpl.java`
- Search `EsSearchComponent.java`

已实施：

- Java 与 Python 双侧向量索引 mapping/维度/provider/model/schema 契约校验。
- 启动时发现不匹配会明确失败或在允许的本地重建路径中重建，避免查询向量和历史索引静默混用。
- ES kNN + BM25 + metadata filter + RRF/rerank 混合检索。
- `_source`/字段约束和 keyword/filter 语义收紧。
- 无外部 embedding key 时提供确定性 local-hash embedding，保证本地部署和测试可运行。
- embedding 健康指标和契约测试。
- 本地实测索引 310 个文档，1024 维契约一致。

不要误把本机的 `DK-pgvector` 容器当成 AI_Shop 依赖。它属于另一个 Deep Knowledge 项目。

### 4.4 Agent 降级、安全与可观测性

已实施：

- 没有 `LLM_API_KEY` / `MEMORY_LLM_API_KEY` 时，意图分类、查询改写、记忆压缩和摘要走确定性降级，不再由异步 Worker 持续报错。
- HTTP 客户端、MCP 内部认证、委托用户身份与旧配置兼容性补充测试。
- Agent/MCP/Worker 结构化日志和 OpenTelemetry 配置。
- 健康接口增加关键依赖、向量契约和可选能力状态。
- Agent 队列、消息状态机、购物任务、画像、售后、视觉消费等路径补充边界修复与测试。

### 4.5 电商业务一致性

已实施的重点包括：

- 退款 Saga 恢复与回滚阶段处理。
- 支付超时/物流/确认消息的死信和恢复。
- 退款库存、退款结果消费幂等。
- 订单自动收货对账任务。
- 订单生命周期通知通过可靠发布器发送。
- 优惠券内部授予返回结构化结果，处理重复和失败语义。
- 用户签到事件持久化、会员等级奖励领取幂等。
- 用户临时封禁到期对账任务。
- 用户通知持久化、去重和消费修复。
- 商品索引文本清洗，避免脏控制字符污染索引。
- 支付宝配置缺失时明确降级/失败，不伪装真实支付成功。

### 4.6 启停、可观测与 CI

已实施：

- `start.sh` 大幅增强：动态端口、PID 启动时间防复用、自动判断 JAR 是否过期、串行构建与启动、依赖健康门禁、凭据生成、数据库身份 provision、RabbitMQ policy、ES 契约和 demo 数据校验。
- `stop.sh` 与 `deploy/service-process-registry.sh` 安全识别由本项目启动的进程。
- Prometheus target 根据动态端口渲染。
- 增加 Loki、Alloy、Grafana datasource、告警和相关契约测试。
- CI/nightly/security workflow 调整。
- dependency exception 结构、过期检查和生成脚本增强。
- 双前端增加测试/构建预算，商城 PWA 资产修复。

## 5. 已完成验证与精确结果

以下结果均在本轮代码工作区、2026-08-16 交接前完成。

### Python Agent

```bash
cd AI_Shop-backend/AI_Shop-agent
set -a
source ../../run/runtime.env
set +a
/home/song/miniconda3/envs/shop/bin/python -m pytest -q
```

结果：`1075 passed, 7 skipped, 1 warning in 8.62s`。

7 个 skip 是显式标记的真实 MySQL migration 测试，随后已单独执行：

```bash
RUN_AGENT_MIGRATION_TESTS=1 \
MYSQL_USER=root \
MYSQL_PASSWORD="$MYSQL_ROOT_PASSWORD" \
/home/song/miniconda3/envs/shop/bin/python \
  -m pytest -q tests/integration/test_migrations.py
```

结果：`8 passed in 25.87s`。

Ruff：

```bash
/home/song/miniconda3/envs/shop/bin/python -m ruff check app tests
```

结果：`All checks passed!`

### 仓库脚本契约测试

```bash
/home/song/miniconda3/envs/shop/bin/python -m pytest -q scripts/test_*.py
```

结果：`48 passed in 0.58s`。

### Java 全量验证

```bash
cd AI_Shop-backend
mvn --batch-mode --no-transfer-progress verify
```

结果：`BUILD SUCCESS`，Reactor 26/26 模块成功，总耗时约 42 秒。
输出中的部分异常栈来自故意验证失败路径的测试，不是测试失败。

### Java Testcontainers 集成测试

```bash
cd AI_Shop-backend
mvn --batch-mode --no-transfer-progress \
  -Pintegration \
  -pl AI_Shop-common,AI_Shop-order/app \
  -am verify
```

结果：`BUILD SUCCESS`。

- `MiddlewareIT`: 7/7
- `TransactionPersistenceIT`: 3/3
- 总耗时约 1 分 23 秒

### 真实 RabbitMQ 集成测试

先从 `run/runtime.env` 加载本地 RabbitMQ 端口和凭据，再运行：

```bash
cd AI_Shop-backend
RUN_RABBIT_INTEGRATION=1 \
mvn --batch-mode --no-transfer-progress \
  -Dtest=MqPublisherConfirmRabbitIntegrationTest,OrderGrowthRabbitIntegrationTest \
  test
```

结果：`BUILD SUCCESS`。两组测试均 1/1，通过了真实 `127.0.0.1:5673`
连接、Publisher Confirm 和不可路由消息 return 捕获。

### 商城前端

```bash
cd AI_Shop-front/AI_Shop-web
npm run test -- --run
npm run lint
npm run build
```

结果：

- 13 个测试文件，33 个测试通过。
- lint 通过。
- build 通过，PWA 生成成功，bundle budget 通过。

### 管理前端

```bash
cd AI_Shop-front/AI_Shop-admin
npm run test -- --run
npm run lint
npm run build
```

结果：

- 1 个测试文件，7 个测试通过。
- lint 通过。
- build 通过，bundle budget 通过。
- Vite 仍提示少数 chunk 超过 400 KB，最大约 1.1 MB。当前不是构建失败，但应列入性能审计。

### 完整本地部署

```bash
./start.sh
```

该次启动自动发现源码比 JAR 新并重建，随后完整成功：

- MySQL/Nacos/Seata schema 与账号准备完成。
- RabbitMQ quorum dead-letter policy 应用完成。
- ES 副本调整为单节点本地值。
- Observability 全栈启动。
- 47 个商品、136 个属性值、135 个 SKU、487 个静态资产校验通过。
- `aishop_vectorstore` 共 310 个文档，1024 维 embedding contract 匹配。
- Agent Alembic migration 完成，7 个 category need schema seed 完成。
- 10 个 Java 服务、MCP、Agent、Worker、商城和管理前端全部启动。

本机这次动态端口：

- 商城 `http://127.0.0.1:6001`
- 管理端 `http://127.0.0.1:6002/admin/`
- Gateway `http://127.0.0.1:8080`
- Agent `http://127.0.0.1:7050/health`
- Worker metrics `http://127.0.0.1:7051/metrics`
- MCP 端口 `7060`
- Product `8099`
- Stock `8102`
- User `8105`
- Cart `8086`
- Order `8093`
- Pay `8096`
- Coupon `8089`
- Search `8108`
- Admin `8111`
- RabbitMQ AMQP `5673`，管理台 `15673`
- Redis `6380`
- Elasticsearch `9200`
- Nacos `8848`
- Sentinel `8858`
- Seata TC `10.255.255.254:8092`
- Prometheus `9093`
- Grafana `3000`
- Tempo `3200`
- Loki `3100`

端口只是本机快照；新机器必须以新生成的 `run/runtime.env` 和启动摘要为准。

健康复核结果：

- Gateway、Product、Stock、User、Cart、Order、Pay、Coupon、Search、Admin 均返回 `200/UP`。
- Agent ready、商城、管理端、Worker metrics 均可访问。
- MCP 的普通 `/health` 请求返回 401/404，因为它不是公开健康端点；不要据此直接判定 MCP 未启动。启动脚本有自己的 readiness 检查。后续可评估是否增加独立、最小暴露的健康端点。

### Demo 与真实浏览器链路

```bash
/home/song/miniconda3/envs/shop/bin/python \
  scripts/bootstrap_demo.py --wait-seconds 180
```

结果：

- 12 个知识文档已存在并发布。
- 向量索引 310 个文档，demo FAQ 6 个。
- 知识契约：12 docs、75 chunks、1024 dims。
- demo 用户具备地址 1、收藏 4、浏览 7、购物车 3、优惠券 5、订单 7、待评价订单 1、通知 5。
- AI demo message 69 完成，回复长度 5153。

真实后端移动端 Playwright：

```bash
cd AI_Shop-front/AI_Shop-web
AISHOP_LIVE_E2E=true \
PLAYWRIGHT_BASE_URL=http://127.0.0.1:6001 \
npx playwright test tests/e2e/live-ai.spec.ts --project=mobile
```

结果：`4 passed (13.2s)`。

覆盖：

- 退款问题解决。
- 模糊售后持久化和幂等选择。
- 推荐曝光、点击、结账和订单归因。
- 清空会话但保留购物画像。

标准 Mock Playwright：

```bash
PLAYWRIGHT_BASE_URL=http://127.0.0.1:6001 npx playwright test
```

结果：`8 passed, 10 skipped (4.8s)`。skip 来自 live E2E 未启用和 project 条件，不是失败。

## 6. 本机运行状态快照

交接时 AI_Shop Docker 容器仍在运行：

- `aishop-mysql`
- `aishop-redis`
- `aishop-rabbitmq`
- `aishop-nacos`
- `aishop-es`
- `aishop-sentinel`
- `aishop-seata`
- `aishop-tempo`
- `aishop-otel-collector`
- `aishop-loki`
- `aishop-alloy`
- `aishop-prometheus`
- `aishop-grafana`

Java、Agent、Worker、MCP 和两个 Vite 进程也由 `start.sh` 管理，PID/日志位于被忽略的 `run/`。

本机同时运行 Deep Knowledge、Damai 和 ROS 等无关容器。特别注意：

- `DK-pgvector` 是其他项目，不属于 AI_Shop。
- `damai-qdrant-1` 是其他项目且当时 unhealthy，不属于 AI_Shop。
- 不要停止、删除或复用这些无关容器。
- 用户提到的 `shop` Docker 容器不存在。

## 7. 已知降级、边界与不得夸大的证据

1. `VISUAL_API_KEY` 未配置，视觉商品搜索状态为
   `DISABLED/DEGRADED`。这是可选外部能力的显式降级，不影响文本商品搜索和主要 AI 客服链路。
2. 外部 LLM/Embedding/Rerank、支付宝、邮件、地图、图片审核等生产密钥不会被提交。缺失时只能验证本地确定性降级或沙箱边界。
3. README 中 RAG v4 的正式 Retrieval/Generation 结果仍是 `FAILED_RETAINED`，人工盲评仍为
   `HUMAN_REVIEW_PENDING`。本地链路可运行不等于检索质量已经过正式门禁。
4. 当前向量数据只有约 310 条，知识库 12 docs / 75 chunks。不能据此声称大规模向量检索容量。
5. `bootstrap_demo.py` 输出 `context enrichment 0/75`。启动不因此失败，但接手者应检查
   `ContextPrefixEnricher` 是否只针对特定新文档生效，还是历史 chunk 没有按预期回填。
6. 管理前端存在 >400 KB 的大 chunk 警告，应评估路由级懒加载和 ECharts/编辑器拆包。
7. 单机 Compose 是开发/演示拓扑，不是生产高可用拓扑。
8. Prometheus/Grafana/Loki/Tempo 已能启动和采集，但尚未做长时间负载、告警触发和恢复演练。
9. MCP 普通 `/health` 语义不清晰，后续可补独立 readiness/liveness 契约及测试。
10. 视觉上传聊天链路在 README 中仍标为尚未接通，不要把视觉索引能力描述成完整用户功能。

## 8. 技术选型初步判断

这一部分是待接手 AI 通过联网官方资料重新核验后形成正式报告的起点，不是最终用户报告。

### RabbitMQ 对比 RocketMQ

当前倾向：保留 RabbitMQ，不立即迁移 RocketMQ。

项目目前的消息场景主要是：

- 电商领域事件。
- 订单/支付/退款/库存的最终一致性。
- 延迟重试和死信。
- Outbox 可靠发布。
- 中低规模 AI 异步队列。
- Java Spring AMQP 生态。

经过本轮加固后，RabbitMQ 已具有：

- Publisher Confirm/Return。
- quorum queue。
- at-least-once dead lettering。
- retry topology。
- consumer idempotency。
- Outbox lease/backoff/manual replay。
- 真实 broker 集成测试。

迁移 RocketMQ 会引入客户端、拓扑、运维、监控、重放和故障模型的整体变化。除非后续有明确指标证明需要
更大的顺序流量、超大规模定时消息、日志型积压/回放或 RabbitMQ 已成为容量瓶颈，否则迁移收益不足以覆盖复杂度。

正式报告必须联网查证最新官方资料，并给出可量化的触发条件，例如：

- 峰值与持续 TPS。
- 单队列 backlog 和恢复时间。
- 严格顺序的分区/业务键数量。
- 延迟消息数量与最长延迟。
- 消息保留/回放窗口。
- 节点数、故障域、RTO/RPO。
- 团队现有 RabbitMQ/RocketMQ 运维能力。

建议只使用 RabbitMQ 和 Apache RocketMQ 官方文档作为主要事实来源，并标注查询日期。

### Elasticsearch 对比 pgvector / 专用向量数据库

当前事实：AI_Shop 使用 Elasticsearch `dense_vector`，没有使用 pgvector。

当前倾向：在现阶段保留 ES。

原因起点：

- 电商商品检索本来就需要 BM25、中文分词、过滤、排序、聚合和元数据。
- AI 导购/RAG 需要 lexical + vector hybrid；在一个搜索引擎内完成可以减少双写和一致性成本。
- 当前数据规模很小，ES 8.19 的 `dense_vector`/HNSW 能覆盖现有需求。
- 本轮已经加入跨 Java/Python 的 embedding/index contract，降低了静默错配风险。

pgvector 更适合需要向量与关系数据强事务共置、团队只愿维护 PostgreSQL、数据规模和检索复杂度适中的场景。
Qdrant/Milvus 等专用向量库更适合向量工作负载需要独立扩缩容、多向量/复杂 ANN、大规模集合或与商品全文搜索隔离时。

正式报告应通过 Elasticsearch、pgvector、Qdrant、Milvus 官方文档重新核验：

- hybrid search 与 filter 行为。
- HNSW/IVF 索引和过滤后的召回特性。
- 更新/删除与重建成本。
- 多租户与数据隔离。
- 内存和磁盘成本。
- 备份、恢复、跨集群复制。
- 现有 310 文档、未来 10 万/100 万/1000 万向量下的分阶段方案。

推荐形成“继续使用 ES + 保持存储抽象 + 达到阈值再做基准测试”的结论，而不是为使用专用向量库而使用。

## 9. 新机器环境准备

建议 Linux 或 WSL2，至少 16 GB RAM、4 核；完整栈更推荐 24-32 GB。Docker Desktop/Engine 应给足内存和磁盘。

必需命令：

- Git
- Bash
- Docker Engine/Desktop 及 Compose v2
- JDK 17+，推荐 21 LTS
- Maven 3.9+
- Python 3.11-3.13
- Node 20.19+ 或 22.12+
- npm
- `curl`
- `openssl`
- `ip`（iproute2）
- `ss`
- `flock`
- `timeout`

推荐：

- `jq`
- `lsof`
- `nc`
- Conda/Mamba
- MySQL CLI

### Python 环境

Conda 方案：

```bash
conda create -n shop python=3.12 -y
conda activate shop
cd AI_Shop-backend/AI_Shop-agent
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

普通 venv 方案：

```bash
python3.12 -m venv AI_Shop-backend/AI_Shop-agent/.venv
AI_Shop-backend/AI_Shop-agent/.venv/bin/python -m pip install --upgrade pip
AI_Shop-backend/AI_Shop-agent/.venv/bin/python -m pip install -e \
  "./AI_Shop-backend/AI_Shop-agent[dev]"
export AISHOP_PYTHON="$PWD/AI_Shop-backend/AI_Shop-agent/.venv/bin/python"
```

`start.sh` 默认只自动发现 Conda 的 `shop` 环境；使用 venv 时必须显式设置 `AISHOP_PYTHON`。

### 前端依赖

```bash
cd AI_Shop-front/AI_Shop-web
npm ci
npx playwright install chromium

cd ../AI_Shop-admin
npm ci
```

### Java 与 Docker 首检

```bash
java -version
mvn -version
node --version
npm --version
python --version
docker version
docker compose version
docker info
```

确认没有关键端口冲突。`start.sh` 会动态顺延多数端口，但 Seata 地址、Docker 端口映射和 WSL 网络仍需实际检查。

## 10. 新机器恢复与启动顺序

1. 不要复制旧机器的 `run/` 和 secrets。
2. 可选复制私有 Agent 配置为
   `AI_Shop-backend/AI_Shop-agent/.env`，但只能通过安全渠道传递，绝不能提交。
3. 无任何外部 AI key 也可启动确定性本地降级；视觉、真实模型、支付等能力会明确降级。
4. 安装 Python 与前端依赖。
5. 执行：

   ```bash
   ./start.sh --build
   ```

6. 启动脚本应自动完成：

   - 动态端口分配和 `run/runtime.env` 写入。
   - 本地秘密生成。
   - Docker 中间件和可观测栈启动。
   - MySQL 元数据、最小权限账号和 migration。
   - RabbitMQ policy。
   - Java 构建与服务启动。
   - MCP、Agent、Worker、前端启动。
   - 商品、知识库、ES 向量契约检查。

7. 只需中间件时：

   ```bash
   ./start.sh --middleware-only
   ```

8. 停止应用但保留中间件：

   ```bash
   ./stop.sh
   ```

9. 连中间件一起停止：

   ```bash
   ./stop.sh --middleware
   ```

10. 端口和秘密都从 `run/runtime.env` 读取，切勿把该文件内容贴入 issue、报告或对话。

## 11. 接手后的验证顺序

先做低成本静态/单元测试：

```bash
git diff --check

cd AI_Shop-backend/AI_Shop-agent
python -m ruff check app tests
python -m pytest -q

cd ..
mvn --batch-mode --no-transfer-progress verify
```

回到仓库根目录后：

```bash
cd /absolute/path/to/AI_Shop
python -m pytest -q scripts/test_*.py
python scripts/check_dependency_exceptions.py
```

前端：

```bash
cd AI_Shop-front/AI_Shop-web
npm run lint
npm run test
npm run build

cd ../AI_Shop-admin
npm run lint
npm run test
npm run build
```

启动后再做：

```bash
python scripts/bootstrap_demo.py --wait-seconds 180
```

加载 `run/runtime.env` 后执行真实 MySQL、RabbitMQ 和 Playwright 测试。命令参考第 5 节，但不要硬编码旧机器密码或动态端口。

## 12. 下一阶段工作清单

### P0：确认汇总提交可复现

- 新机器 clean clone。
- 安装依赖。
- `./start.sh --build`。
- 全部健康门禁。
- Agent/Java/前端基础测试。
- Live Playwright 关键链路。

### P1：完成联网技术选型报告

- 只使用或优先使用官方一手资料。
- RabbitMQ：quorum queue、publisher confirm、dead lettering、streams、性能/集群边界。
- RocketMQ：事务消息、顺序消息、定时/延迟消息、存储模型、NameServer/Broker/Proxy 运维。
- Elasticsearch：`dense_vector`、kNN filter、hybrid/RRF、量化、资源模型。
- pgvector：HNSW/IVFFlat、filter、迭代扫描、事务共置。
- Qdrant/Milvus：filter、多向量、分布式扩缩容和运维。
- 给出当前、增长期和大规模三个阶段的建议，以及切换触发指标。
- 最终回答必须明确：当前没有 pgvector；ES 存向量是有意且现阶段合理；当前不建议 RabbitMQ 换 RocketMQ。

### P1：第二轮代码与基础设施深审

- 检查启动后新增日志中的 ERROR/FATAL/Traceback。
- 检查所有 TODO/FIXME、硬编码凭据、默认密码、生产绕过开关。
- 核对 MCP liveness/readiness 契约。
- 深查 `context enrichment 0/75`。
- 检查 Outbox 租约并发、时钟偏移、重放权限和跨服务幂等键冲突。
- 检查 RabbitMQ retry queue 数量、TTL、DLX cycle 和队列 policy 匹配范围。
- 检查 ES mapping 变更、alias/rollover、重建期间可用性和备份恢复。
- 检查 Redis key TTL、分布式锁 lease/watchdog 和 cluster 模式兼容。
- 检查 Seata 单点、undo_log 清理、超时与长事务。
- 检查 MySQL 8.4 SQL mode、索引、慢查询、连接池总量。
- 检查管理前端大 chunk。
- 运行 dependency exception 实际检查和过期项审计。
- 检查告警规则是否可在真实故障演练中触发并恢复。

### P2：基准与容量

- RabbitMQ 端到端吞吐、积压恢复、重复投递率、失败重放。
- ES 10 万/100 万向量 synthetic benchmark，记录 Recall/latency/memory。
- Java 10 服务总连接池与 MySQL max connections 压力。
- Agent 并发对话、Worker backlog、LLM 降级和超时。
- 前端包体、首屏和弱网指标。

## 13. 建议最终向用户汇报的结构

不要直接展示本文。最终用户报告建议按以下顺序：

1. 已实施修复摘要。
2. 本地部署与测试证据。
3. RabbitMQ vs RocketMQ 最终结论及迁移触发条件。
4. ES dense_vector vs pgvector/专用向量库结论。
5. 第二轮新发现，按严重度排序。
6. 仍然存在的真实边界和未验证项。
7. 可执行的近期/中期路线图。

避免：

- 把本地单节点说成生产高可用。
- 把 deterministic fallback 说成真实模型效果。
- 把 smoke test 说成容量验证。
- 把另一个项目的 pgvector/Qdrant 容器说成 AI_Shop 依赖。
- 宣称正式 RAG 质量门禁已经通过。

## 14. 安全规则

- 不提交 `run/`、`.env`、私钥、数据库 dump、日志或凭据。
- 不在终端输出或用户回复中泄露 `run/runtime.env`。
- 不把 example 中的默认值用于生产。
- 不开启生产认证绕过：
  `AISHOP_DEV_LOGIN_BYPASS`、`ALLOW_DEVELOPMENT_AUTH_BYPASS`。
- 不使用 `git reset --hard`、`git checkout --` 等覆盖用户工作区。
- 发现非本任务改动时保留并与其协作，不擅自回滚。

## 15. 最后已知结论

- 代码层面的主要可靠性、安全、配置、部署和观测问题已经实施修复。
- 本地完整栈已经成功部署，核心电商 + AI 客服导购链路已通过真实浏览器测试。
- `shop` 不是 Docker 容器，而是 Conda Python 环境。
- AI_Shop 没有 pgvector；向量由 Elasticsearch `dense_vector` 存储。
- 现阶段保留 Elasticsearch 和 RabbitMQ 的初步判断成立，但必须由接手 AI 完成联网官方资料复核和最终书面论证。
- 仍需完成第二轮深审、必要修复和最终面向用户的全面报告。
