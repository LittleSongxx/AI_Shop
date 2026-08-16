# AI_Shop — AI 驱动的微服务电商平台

> 内容状态：当前有效
>
> 本轮实施基线：`ef9aa0659a9275a99bb74cdb46e87770150dea0a` + 实施前 workspace diff SHA（见 [证据 manifest](docs/evidence-manifest.json)）
>
> 最后核验时间：2026-08-14（Asia/Shanghai）
>
> 适用环境：本地演示、开发与 CI；不代表生产容量、业务收益或线上 SLO 证明

基于 Spring Cloud Alibaba + Python LangGraph 构建的全栈微服务电商项目，集成 AI 购物导购、智能客服与 RAG 知识库。工程化基础扎实：分布式事务、消息可靠投递、分布式锁、安全过滤均有具体实现。

---

## 技术栈

### 后端 · Java 微服务

| 层面 | 技术选型 |
|------|---------|
| 框架 | Spring Boot 3 · Spring Cloud Alibaba |
| 注册/配置 | Nacos |
| 网关 | Spring Cloud Gateway |
| 分布式事务 | Seata（AT 模式 + `@GlobalTransactional`） |
| 消息队列 | RabbitMQ + 本地事务消息表 + 补偿任务 |
| 缓存 | Redis（Lua 原子操作、分布式锁、Bitmap 签到） |
| 持久层 | MyBatis（自定义泛型 Mapper 基类） |
| 搜索 | Elasticsearch（IK 分词） |
| 熔断限流 | Sentinel |
| 支付 | 支付宝沙箱（PC 网页支付） |

### AI 服务 · Python

| 层面 | 技术选型 |
|------|---------|
| 框架 | FastAPI + LangGraph（ReAct Agent） |
| LLM | OpenAI 兼容接口（可接任意模型） |
| RAG | Elasticsearch 向量检索 + BM25 + RRF/rerank 混合 |
| 工具调用 | MCP（Model Context Protocol）双向通信 |
| 会话记忆 | Redis 短期 + MySQL 长期持久化 |
| 可观测 | OpenTelemetry（OTLP）+ Prometheus 指标 |
| 评测与测试 | pytest + `aishop-eval/v1` 统一 Runner；精确结果、命令和证据边界见 [AI 应用求职项目证据总览](docs/AI应用求职项目证据总览.md) |

### 前端

| 模块 | 技术选型 |
|------|---------|
| 用户端 | Vue 3 + Vite + Element Plus |
| 管理后台 | Vue 3 + Vite + Element Plus |

### 基础设施

Docker Compose：MySQL 8.4.11 · Redis 7.4.7 · RabbitMQ 4.2.9 · Nacos 2.5.3 ·
Elasticsearch 8.19.19（IK）· Sentinel 1.8.8 · Seata 2.5.0

---

## 项目结构

```
AI_Shop/
├── AI_Shop-backend/              # Java 微服务
│   ├── AI_Shop-gateway/          # 统一入口：路由、鉴权、内部 Token 校验
│   ├── AI_Shop-common/           # 公共组件：Redis 工具、事务消息、异常体系
│   ├── AI_Shop-user/             # 用户：注册登录、签到、会员等级、地址
│   ├── AI_Shop-product/          # 商品：分类、SKU、属性、图片
│   ├── AI_Shop-stock/            # 库存：悲观锁扣减、超卖防护
│   ├── AI_Shop-cart/             # 购物车
│   ├── AI_Shop-order/            # 订单：普通下单 + 优惠券秒杀下单
│   ├── AI_Shop-pay/              # 支付：支付宝 PC 网页支付、回调验签
│   ├── AI_Shop-coupon/           # 优惠券：发放、抢购、用券
│   ├── AI_Shop-search/           # 搜索：ES 全文检索、热词统计
│   ├── AI_Shop-admin/            # 管理后台 API
│   └── AI_Shop-agent/            # Python AI 服务（LangGraph ReAct Agent）
├── AI_Shop-front/
│   ├── AI_Shop-web/              # 用户端 Vue 3
│   └── AI_Shop-admin/            # 管理后台 Vue 3
├── deploy/                       # Docker Compose、Nginx 示例、上线清单
└── sql/                          # 初始化 DDL
```

---

## 核心功能

### 业务底座

- **完整下单链路**：浏览 → 加购 → 创建订单（Seata 全局事务）→ 支付宝支付 → 回调核销 → 发货 → 签收 → 评价
- **优惠券秒杀**：Redis Lua 原子预占 + DB 库存双重校验，`CouponRushOrderService` 管理完整生命周期
- **消息可靠性**：本地消息表 + `TransactionalMqSender`（事务提交后发送）+ MQ 补偿扫描，三层保障
- **支付生命周期锁**：Redis 互斥锁确保支付回调、超时关单、迟到退款三路并发只有一路生效
- **退款 Saga 人工复核闭环**：重试耗尽进入 MANUAL_REVIEW 后由管理端审批——通过则按原阶段恢复、对账定时器自动续跑，驳回则本次申请作废、用户可重开；审批用客户端幂等键 + CAS 保证并发单次生效，台账落账，恢复前重新校验冻结字段（金额/数量/属性）防人工窗口期数据漂移
- **签到系统**：Bitmap 月度记录 + Hash 计数器 + Lua 原子操作，补签次数按累计天数兑换
- **敏感词过滤**：DFA 算法，支持管理员动态维护词库

### AI 购物导购与客服

- **ReAct Agent**：基于 LangGraph 的有状态对话，支持商品推荐、订单查询、物流查询、售后引导
- **Workflow / Agent 分界**：确定性状态流使用规则或 Workflow；只有开放决策、职责隔离或可并行任务进入 bounded specialist，保留 legacy single-agent 回退
- **RAG 知识库**：12 份项目级业务文档、75 个 chunk、6 个 FAQ；Exact FAQ、自适应 BM25/Vector、RRF、Rerank、最小充分证据、逐事实句引用和有界 repair
- **MCP 工具链**：结构化工具调用（查询订单/物流/券/商品），结果以卡片形式渲染至前端
- **输入防护**：NFKC 归一化 + 两级规则（硬阻断 / 可疑累积）+ Propose→用户确认→Java 执行，防 Prompt 注入
- **强制工具回退**：模型跳过必要工具时，框架层自动补全并路由至 finalize，避免幻觉回复
- **工具结果 Observation 层**：所有进上下文的工具输出统一脱敏（手机号/邮箱/身份证）、长度裁剪，被省略部分写入 trace
- **通道污染检疫**：与输入防护共用规则表；RAG 片段在组装点逐条剔除，工具结果命中注入话术即替换为隔离占位符，污染项计数入指标，隔离不等于整链拒绝
- **Prompt 单一事实源**：fragment 注册表统一管理全部提示词片段（redis 覆盖可观测），每轮对话记录 selectedFragments 到决策 trace
- **per-request 成本摘要**：contextvar 隔离的轻/重路径成本累计（LLM 调用数、token、金额、模型），异步调度任务与对话路径隔离
- **委托身份信道**：`X-Agent-User-Id` 系统信道头为权威（模型不可见），body userId 仅作参考；缺失 401、不一致 403、归属不符 403，fail-closed
- **统一评测与消融**：Commerce、安全、Search/RAG 统一输出 case/summary/report；单 Agent、多 Agent、Workflow 由独立进程对照，临时结果不入库，baseline 只能显式接受
- **熔断降级**：Circuit Breaker 包装外部 LLM 调用，超时自动降级
- **速率限制**：用户级别双窗口限流，防止滥用

### 治理与用户闭环

- **管理员 RBAC**：`SUPER_ADMIN`、`AI_OPERATOR`、`SUPPORT_AGENT`、`DATA_ANALYST`、`AUDITOR` 五角色，Redis 会话绑定主体版本，Controller 和 Python 管理接口均执行权限校验
- **内部管理员断言**：Java → Python 使用 HMAC-SHA256，覆盖 method、path、body hash、管理员、角色、权限、时间戳和 nonce，支持 current/previous 密钥轮换与重放拒绝
- **试用与指标**：批次、参与者、`SYNTHETIC`/`LOCAL_PILOT`/`REAL_USER` 来源、verified success、FCR、TTFT、token、成本和匿名 JSON/CSV/Markdown 报告
- **隐私中心**：用户可异步导出或彻底删除 AI 数据，支持密码二次确认、`Idempotency-Key`、分步骤恢复和短期下载；订单/支付保留数据解除 AI 关联并匿名化
- **分层 CI 与供应链**：PR 跑确定性 AI Runner、Java unit/IT、双前端、Mock Playwright 和 SBOM；nightly 扩展回归，weekly 漏洞扫描，真实模型工作流缺 secrets 时明确“未采集”

---

## 快速启动

### 前置依赖

- JDK 17+、Maven 3.9+
- Python 3.11–3.13
- Docker & Docker Compose

### 0. 一键起停（推荐）

```bash
./start.sh --build          # 首次：构建 + 中间件 + 可观测栈 + Java 微服务 + Agent
./start.sh                  # 后续：复用最新 JAR；源码较新时自动串行重建
./start.sh --middleware-only  # 只起 Docker 中间件
./stop.sh                   # 停 Java + Agent（中间件保留）
./stop.sh --middleware      # 一并停中间件
```

`start.sh` 会自动将业务与可观测组件的冲突端口向后顺延，实际端口写入
`run/runtime.env`；随后等待
MySQL / Nacos / Seata 就绪，再串行拉起 Java 服务以控制 WSL 峰值内存。非
`--middleware-only` 启动还会预检 Agent `.env`：填写 `RERANK_API_KEY` 后，必须同时把
`RERANK_BASE_URL` 中的 `YOUR_WORKSPACE_ID` 换成真实百炼业务空间 ID。PID 和日志落在
`run/`，Agent 使用 conda 环境 `shop`。下面 1–5 步是拆开的手工流程，排查问题时用。

### 1. 启动中间件

```bash
./start.sh --middleware-only
```

等待 Nacos（8848）、MySQL（3306）、Redis（**6380**）、RabbitMQ（**5673**）、ES（9200）全部就绪。

> Redis / RabbitMQ / Seata 的宿主机端口是 +1 偏移（6380 / 5673 / 8092），避免和本机已装的同类服务冲突。
> Seata 2.5 还要求注册地址不能是 `127.0.0.1`，因此不要绕过启动脚本直接执行裸 Compose；
> 跨主机部署时显式设置可达且受防火墙保护的 `SEATA_IP`。详见
> [deploy/本地中间件启动指南.md](deploy/本地中间件启动指南.md)。

### 2. 初始化数据库

```bash
bash deploy/init-mysql-meta.sh
```

该脚本初始化数据库、Nacos/Seata 元数据和 undo_log；业务表由各 Java 服务的
`R__current_schema.sql` 在启动时幂等迁移，Agent 表由 Alembic 迁移。

### 3. 构建 Java 微服务

```bash
cd AI_Shop-backend
mvn -DskipTests package
```

按顺序启动各模块，详见 [deploy/start-hint.sh](deploy/start-hint.sh)。

### 4. 启动 AI Agent 服务

```bash
cd AI_Shop-backend/AI_Shop-agent

python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env   # 填入 LLM API Key、DB 连接、内部 Token

uvicorn app.main:app --host 0.0.0.0 --port 7050   # API 服务
python -m app.worker                               # 异步 Worker（另一个终端）
```

### 5. 启动前端

```bash
cd AI_Shop-front/AI_Shop-web   && npm install && npm run dev   # 用户端
cd AI_Shop-front/AI_Shop-admin && npm install && npm run dev   # 管理后台
```

---

## 关键环境变量

| 变量名 | 说明 |
|--------|------|
| `AISHOP_INTERNAL_TOKEN` | 服务间调用 `/internal/**` 的共享密钥（全服务一致） |
| `AISHOP_INTERNAL_OPS_TOKEN` | Outbox 人工重放等高风险运维接口的独立密钥 |
| `AISHOP_ADMIN_ASSERTION_CURRENT_SECRET` | Java 管理端到 Python Agent 的管理员断言签名密钥 |
| `PILOT_IDENTITY_HMAC_SECRET` | 试用参与者稳定伪名匹配密钥，不随请求签名密钥轮换 |
| `PRIVACY_EXPORT_SIGNING_SECRET` | AI 数据删除后保留事实匿名化使用的独立密钥 |
| `ADMIN_PASSWORD` | 管理后台初始管理员密码；生产环境必须替换默认值 |
| `MYSQL_ROOT_PASSWORD` / `MYSQL_USER` / `MYSQL_PASSWORD` | root 仅用于本地引导；业务服务使用只有 DML 权限的非 root 运行账号 |
| `FLYWAY_USER` / `FLYWAY_PASSWORD` | Java Flyway 与 Agent Alembic 共用的独立迁移身份，不得与业务运行账号复用 |
| `NACOS_MYSQL_*` / `SEATA_MYSQL_*` | Nacos、Seata 各自的 schema 级数据库账号；一键脚本自动生成并持久化 |
| `RABBIT_PASSWORD` / `REDIS_PASSWORD` | RabbitMQ 与 Redis 凭据；`start.sh` 会为本地 Redis 生成并持久化随机密码 |
| `SEATA_SECURITY_SECRET_KEY` | Seata TC Token 签名密钥 |
| `GRAFANA_ADMIN_PASSWORD` | 可选；一键脚本未收到显式值时自动生成并保存到 `run/secrets/grafana.env` |
| `APP_ENV` / `AISHOP_PRODUCTION_READY` | 生产环境分别设为 `production` / `true`，触发 Python Agent 与 Java 服务的安全校验 |
| `AISHOP_DEV_LOGIN_BYPASS` | 本地调试开关，**禁止生产开启** |
| `ALLOW_DEVELOPMENT_AUTH_BYPASS` | Python Agent 的开发认证绕过开关，**禁止生产开启** |
| `LLM_BASE_URL` | LLM API 基础地址（OpenAI 兼容） |
| `LLM_API_KEY` | LLM API Key |
| `LLM_MODEL` | 模型名称；当前示例为 `deepseek-chat`，也可填写兼容的其他模型 |
| `EMBEDDING_API_KEY` | 向量检索和知识库索引的 Embedding Key |
| `RERANK_API_KEY` / `RERANK_BASE_URL` | Qwen3 Rerank Key 与百炼业务空间地址；未配置时回退 RRF |
| `MEMORY_LLM_API_KEY` | 可选记忆摘要模型 Key，留空复用 `LLM_API_KEY` |
| `VLM_ENABLED` / `VLM_API_KEY` | 可选视觉模型开关与 Key（Java Search 侧，用于知识文档图片理解）；商城聊天上传链路尚未接通 |
| `ALIYUN_ACCESS_KEY_ID` / `ALIYUN_ACCESS_KEY_SECRET` | 可选 DirectMail 邮箱验证码凭据 |
| `ALIPAY_*` | 真实支付和回调验签凭据 |
| `AMAP_KEY` | 可选高德逆地理编码 Key |
| `BAIDU_AIP_API_KEY/SECRET_KEY` | 可选百度图片审核凭据 |

完整清单见 [AI_Shop-backend/AI_Shop-agent/.env.example](AI_Shop-backend/AI_Shop-agent/.env.example) 和 [deploy/env.production.example](deploy/env.production.example)。

---

## 数据与指标口径

唯一人工证据入口是 [docs/AI应用求职项目证据总览.md](docs/AI应用求职项目证据总览.md)，机器可校验入口是
[docs/evidence-manifest.json](docs/evidence-manifest.json)。证据严格分为源码/测试、确定性合成运行时、
本地集成、配置真实模型和授权真实用户五级；运行 `python scripts/check_evidence_manifest.py` 可检查结构、
数据锁、结果 SHA 和“未采集”边界。

当前 27 条 Commerce runtime、18 条 AI 安全和 162 条 Search/RAG 数据契约属于
`SYNTHETIC + deterministic`。在 `SYNTHETIC + local-live` 层，Search v2 覆盖中文 600 商品/240 查询、
WANDS 42,994 商品全库/202 查询/32,919 条有效人工判断，以及 47 商品目录上的 45 条真实
`ProductService` 路径。中文 fresh Recall@3/5 为 0.8896/0.9969、NDCG@5 为 0.9753；WANDS 只报告
known-relevant、Judged@K、bpref 和 condensed 指标，不把未标注商品当成无关。首次 ProductService
Recall@10 只有 0.3778，正式结果按 `FAILED_RETAINED` 保留；暴露后运行时回归为 45/45、Recall@3/5/10
均 0.9778、MRR@10 0.9444，但明确标记 `freshEvidence=false`。

RAG v4 在 12 份文档、75 chunk、6 FAQ 上实跑 72 public + 144 regression + 48 fresh，共 264 条检索；
fresh Recall@3/5 为 0.8056、MRR@10 为 0.75、NDCG@5 为 0.7645，正式门禁未过。60 条
`deepseek-v4-flash` 生成完整执行，0 runtime error、0 严重安全违规，但只通过 39 条；暴露后零 Provider
重评为 49/60，仍低于 0.85 且不能冒充新 holdout。正式 Retrieval/Generation 均保留
`FAILED_RETAINED`，人工盲评状态为 `HUMAN_REVIEW_PENDING`。
这些结果不代表真实用户或生产效果；Agent 在线模型、可信人民币成本、`REAL_USER`、CTR/CVR、GMV uplift、
线上 SLO 与生产规模仍为“未采集”。详细分组指标、消融、失败 case、Provider 调用和诚实边界见证据总览。历史冻结会话及
旧小样本结果只用于解释项目演进，见 [冻结会话评测限制与变更记录.md](AI_Shop-backend/AI_Shop-agent/benchmarks/冻结会话评测限制与变更记录.md)，
不再作为 README 的当前成绩。

本地全链路部署、RAG、搜索、分类、购物车和管理端滚动问题的真实排障过程见
[docs/项目问题排查与修复复盘.md](docs/项目问题排查与修复复盘.md)。文档保留了错误假设、
证据链、修复取舍与回归数据，可作为面试项目复盘材料；Search/RAG 数据、消融、SHA 和坏例见
[docs/Search与RAG成熟评测报告.md](docs/Search与RAG成熟评测报告.md)。用户已删除的旧审计/决策文档不再作为当前证据入口。

---

## 许可证

见 [LICENSE.md](LICENSE.md)
