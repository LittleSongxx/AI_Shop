# AI_Shop — AI 驱动的微服务电商平台

> 内容状态：当前有效
>
> 当前证据状态：`PUBLISHED_FINAL`；唯一当前结果、数据集哈希和可陈述边界以 [证据 manifest](docs/evidence-manifest.json) 为准
>
> 最后核验时间：2026-08-24（Asia/Hong_Kong）
>
> 适用环境：本地演示、开发与 CI；不代表生产容量、业务收益或线上 SLO 证明

基于 Spring Cloud Alibaba + Python LangGraph 构建的全栈微服务电商项目，当前主线收敛为：

1. **Java 电商底座 → 文本/视觉 AI 推荐导购 → Java 权威价格/库存 → 点击/加购/支付归因**；
2. **AI 客服 → 发布版政策 RAG → Java 订单权威事实 → 用户确认 → 幂等执行 / `INCONCLUSIVE` / `MANUAL_REVIEW`**。

模型只负责受约束的检索、解释和提案；商品、库存、订单、支付和最终写入仍由 Java 领域服务负责。

### 秋招定位

- **主叙述：AI 后端 / Agent 开发**——Agent 边界、RAG/Tool/MCP、可靠执行、真实 badcase、评测和 Trace 闭环。
- **第二入口：Java 电商后端**——订单/库存/支付事务、Redis、RabbitMQ、一致性、幂等和故障恢复。
- **视觉搜索**属于推荐主线；**Text2SQL**目前只是带权限、扫描预算、分页、导出审计的治理实验，未达门槛前不包装为第三主线。

面试可陈述的功能闭环、真实样本和未采集边界见 [AI_Shop 主线与开发记录](docs/project/AI_Shop主线与开发记录.md)。

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
| 评测与测试 | pytest + `aishop-evaluation/v3`；Conda `shop` 环境、quality scorecard（Search 主指标 + badcase）、development/regression 数据锁、一次性 final 和哈希证据包 |

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

- **自然语言订单定位**：根据订单号、相对时间、金额和商品描述筛选权威订单；零候选、多候选、无可操作项和依赖失败分别处理，歧义上下文可跨轮持久化
- **自适应编排**：简单权威查询或参数完整提案走 Workflow，开放/单域 RAG 走单 Agent，订单事实与政策等跨域复合请求才走 bounded multi-agent；四种配置可用于配对消融，但正式 live 配对结果仍未采集
- **受控写操作**：模型只能生成 `PROPOSE_*` 提案；用户确认后 Java 重新校验身份、归属、状态与幂等键，未知远端结果进入 `INCONCLUSIVE`，核对到边界后转 `MANUAL_REVIEW`
- **RAG 知识库**：12 份项目级业务文档、75 个 chunk、6 个 FAQ；Exact FAQ、自适应 BM25/Vector、RRF、Rerank、最小充分证据、逐事实句引用和有界 repair
- **MCP 工具链**：结构化工具调用（查询订单/物流/券/商品），结果以卡片形式渲染至前端
- **输入防护**：NFKC 归一化 + 两级规则（硬阻断 / 可疑累积）+ Propose→用户确认→Java 执行，防 Prompt 注入
- **强制工具回退**：模型跳过必要工具时，框架层自动补全并路由至 finalize，避免幻觉回复
- **工具结果 Observation 层**：所有进上下文的工具输出统一脱敏（手机号/邮箱/身份证）、长度裁剪，被省略部分写入 trace
- **通道污染检疫**：与输入防护共用规则表；RAG 片段在组装点逐条剔除，工具结果命中注入话术即替换为隔离占位符，污染项计数入指标，隔离不等于整链拒绝
- **Prompt 单一事实源**：fragment 注册表统一管理全部提示词片段（redis 覆盖可观测），每轮对话记录 selectedFragments 到决策 trace
- **逐运行预算**：ContextVar 隔离 token、人民币成本、节点步数与 monotonic deadline，80% 预警，超限进入明确受控终态；异步任务与对话互不污染
- **委托身份信道**：`X-Agent-User-Id` 系统信道头为权威（模型不可见），body userId 仅作参考；缺失 401、不一致 403、归属不符 403，fail-closed
- **统一可信评测**：Search、RAG、Agent 共用一个 fail-closed suite；适配器分别调用生产 ProductService、RAG retriever/generation contract 和 Agent HTTP + 持久化 Episode，冻结后 final 只能 claim/执行一次，任何域失败都会阻断发布门禁
- **熔断降级**：外部 Provider 使用 CLOSED/OPEN/HALF_OPEN 熔断与受控 fallback；未知写结果不会被通用重试自动重放
- **速率限制**：用户级别双窗口限流，防止滥用

### 治理与用户闭环

- **管理员 RBAC**：`SUPER_ADMIN`、`AI_OPERATOR`、`SUPPORT_AGENT`、`DATA_ANALYST`、`AUDITOR` 五角色，Redis 会话绑定主体版本，Controller 和 Python 管理接口均执行权限校验
- **内部管理员断言**：Java → Python 使用 HMAC-SHA256，覆盖 method、path、body hash、管理员、角色、权限、时间戳和 nonce，支持 current/previous 密钥轮换与重放拒绝
- **试用与指标**：批次、参与者、`SYNTHETIC`/`LOCAL_PILOT`/`REAL_USER` 来源、verified success、FCR、TTFT、token、成本和匿名 JSON/CSV/Markdown 报告
- **隐私中心**：用户可异步导出或彻底删除 AI 数据，支持密码二次确认、`Idempotency-Key`、分步骤恢复和短期下载；订单/支付保留数据解除 AI 关联并匿名化
- **分层 CI 与供应链**：PR 校验评测契约/数据锁并运行 Java unit/IT、Python、双前端、Playwright 和 SBOM；配置真实 Provider 的工作流缺任何依赖都会失败，不以 skip 伪装成功

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

conda activate shop
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
| `VLM_ENABLED` / `VLM_API_KEY` | 可选视觉模型开关与 Key；视觉找同款走推荐 v1 契约，正式 live 评测禁止 fallback |
| `ALIYUN_ACCESS_KEY_ID` / `ALIYUN_ACCESS_KEY_SECRET` | 可选 DirectMail 邮箱验证码凭据 |
| `ALIPAY_*` | 真实支付和回调验签凭据 |
| `AMAP_KEY` | 可选高德逆地理编码 Key |
| `BAIDU_AIP_API_KEY/SECRET_KEY` | 可选百度图片审核凭据 |

完整清单见 [AI_Shop-backend/AI_Shop-agent/.env.example](AI_Shop-backend/AI_Shop-agent/.env.example) 和 [deploy/env.production.example](deploy/env.production.example)。

---

## 数据与指标口径

人工阅读入口为 [AI_Shop 主线与开发记录](docs/project/AI_Shop主线与开发记录.md)，质量主指标、95% CI 和指标级 badcase 见
[AI质量评测与Badcase](docs/evaluation/AI质量评测与Badcase.md)，机器 scorecard 见
[AI质量评测与Badcase.json](docs/evaluation/AI质量评测与Badcase.json)，机器总索引为
[evidence-manifest.json](docs/evidence-manifest.json)。运行 `python scripts/check_evidence_manifest.py` 会交叉校验 suite、
development/regression 数据锁、文件 SHA、case 数、域分布、集合互斥、失败 final 和文档边界。

客服 intent/风险/slot/handoff 的 60 条双人盲标+第三人仲裁证据见
[客服金标评测](docs/evaluation/customer-service/客服金标评测.md)；本轮核心排错、阶段指标、外部调研和后续优先级已合并到
[主线与开发记录](docs/project/AI_Shop主线与开发记录.md)；新版面试题入口为
[AI应用开发_Java后端_真实面试题与备考报告_20260824.md](AI应用开发_Java后端_真实面试题与备考报告_20260824.md)。

当前评测协议为 `aishop-evaluation/v3`，Python 命令必须使用 Conda `shop` 环境：
`/home/song/miniconda3/envs/shop/bin/python`。development 锁定 `43` 条（Search/RAG/Agent = `18/18/7`），
regression 锁定 `51` 条（`20/26/5`）；可见真实 Provider run 分别为
`development-20260822-ai-quality-v9` 和 `regression-20260822-ai-quality-v9`，源码指纹均为
`e8a2769a3a6a04edfc6978e55d9af935fb43900dcd1afa468f94391f6454ea69`。

当前唯一发布结果是 `release-20260822-ai-quality-v9` / `final-20260822-ai-quality-v9`。主质量结果是 Search
`Recall@10 macro/query=0.962121`、补充的 `Recall@10 micro/qrel=52/56=0.928571`、`MRR@10=0.937500`、
`NDCG@10=0.920521`；scorecard 列出 3 个漏召回 query、4 个漏召回商品和每个排序 badcase。RAG 只保留最小事实安全证据，
Agent 只保留工具契约/延迟诊断；客服当前为 60 条 `HUMAN_VERIFIED` 离线金标，证据包和哈希见
`AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/customer-service/customer-service-human-v1-20260823/`。

RAG answerable retrieval Recall@5 为 `29/29=1.000`，lexical grounded faithfulness/citation/no-answer 均为 `50/50=1.000`；
Search/RAG/Agent 本地完整链路 P50/P95/P99 分别为 `269.6/797.3/940.6 ms`、`1825.4/4246.7/4525.3 ms`、
`1362.5/17077.8/20943.0 ms`。样本小且只作本地诊断，RAG lexical/semantic shadow 也不等于人工语义准确率。

客服规则预路由的历史人工金标基线为 Intent Macro-F1 `0.955299`（3 个 intent badcase，分层 95% CI `[0.929286,0.987409]`）；同一 60 条 HUMAN_VERIFIED gold 的当前规则回放为 Intent Macro-F1 `1.000000`（无 intent badcase，CI `[1.000000,1.000000]`）。后者是同集修复回放，不是新 holdout 泛化结果。高风险 intent Recall `1.000`（10/10）、
完整人工 schema 的 slot micro F1 `0.996364=822/825`（3 个 strict-format badcase，95% CI `[0.995061,0.997636]`）、slot EM `0.911765`（31/34）、
handoff Recall `1.000`（14/14）、严重漏转人工率 `0/6`。
同一 60 条人工 gold 的 paired replay 为 Span F1 `0.907652 -> 0.996364`、EM `0.558824 -> 0.911765`，修复 12 case、回归 0；只剩 `009/020/058` 的金额原始格式差异。该结果是同集优化证据，不是新 holdout。
这些是离线规则预路由质量，不是线上客服成功率；`releaseGateEligible=false` 保持 fail-closed。

历史 HTTP v1 的同一 60 条 gold 已经正式 Agent/Java/RAG/LLM 路径执行 `60/60`，转人工混淆矩阵为 `TP=14,TN=46,FP=0,FN=0`，引用结构违规 `0`。
Episode 槽位经脱敏，因此 HTTP Slot F1/EM 不可测。答案质量已完成双人盲审与 `8` 条独立第三人仲裁：冻结 60 条回放的答案正确率为
`51/60=85.0%`（Wilson 95% CI `73.9%–91.9%`），但可计分引用的语义支持率仅 `6/30=20.0%`（`9.5%–37.3%`），联合质量通过率
`32/60=53.3%`。转人工适当率为 `60/60`，unsafe-answer 为 `0/60`；后两项的小样本区间不能写成“绝对安全”。双人 `52/60` 完全一致和
`8` 条仲裁是标注可靠性，不能当模型准确率。该 HTTP 观察包 `releaseGateEligible=false`，不是 CSAT/FCR、线上成功率或历史 final 的追溯门禁；
引用支持缺口是当前客服主线的最高优先级质量问题。
另有 60 条 v2 draft 及双人盲标表已生成，仲裁前不与当前 60 条 HUMAN_VERIFIED 分母合并。动态业务工具的 Java 权威 `sourceRefs` 已补齐并在最终消息/HTTP 评测 trace 中保留；这只是代码和契约修复，旧 HTTP `6/30=20.0%` 标签仍绑定旧 run，不能迁移到新输出。

修复后已完成一轮新的真实 HTTP observation：`customer-service-http-v13-20260824`。它在同一冻结 60 条上完整终态 `60/60`、HTTP error `0`、行为契约 `10/10`，Provider usage 为 `18` 次调用、输入/输出 token `78,470/5,486`、`costCny=null` / `UNPRICED`；本地 P50/P95/P99 为 `1015.049/11372.651/22858.230 ms`，仅作本机诊断。`60/60`、Intent/slot 指标和行为契约都不是人工最终答案质量。API、Worker、MCP 已增加相同源码 fingerprint 的 readiness/preflight 检查，避免独立 MCP 进程仍加载旧代码。

原始 Provider observation 已作为只读 [pre-evaluator-fix 包](AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/customer-service/customer-service-http-v13-pre-evaluator-fix-20260824/) 保存；其中四条“不能据此断言平台无货”被旧纯正则错误判为“平台无货”断言。正式 [v13 包](AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/customer-service/customer-service-http-v13-20260824/) 仅对同一 observation 做免责声明感知的确定性离线重算，未重跑 Provider。该评测器修复使行为契约从误报恢复，不能表述为模型质量提升。v13 源 report 的答案质量状态仍为 `PENDING_HUMAN_REVIEW`；双人盲审现已封存，案件级一致率 `49/60=81.67%`，生命周期进入 `PENDING_ADJUDICATION`，有 11 条分歧待第三人。详见 [v13 待仲裁证据包](AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/customer-service/customer-service-http-v13-answer-review-pending-adjudication-20260824/) 和根目录 `adjudication.answer-review-v13.open.jsonl`。在仲裁完成前，不能报告新的答案正确率、引用语义支持率、转人工适当率、unsafe rate 或与 v1 的对照。

另保留一对只读运行版本诊断：v11 在旧 MCP 进程仍加载旧源码时，于同一 10 条定向集出现 `6/10` 行为契约违例；完整重启后的 v12 为 `0/10`。二者由 manifest 成对校验相同数据哈希、状态、违例数和 `SHA256SUMS`，仅证明版本一致性修复有效，不构成答案质量分数。已被 v13 覆盖的 v5/v10 60 条中间运行与未完成 v3 盲审草稿已清理。

Search 已对 10 条已知难例做真实成对回放：Recall/MRR/NDCG 与 v9 baseline 的 delta 均为 `0`，硬约束违规 `0`；仍保留 3 个多商品/集合意图/比较对象难例。
该 replay 只证明当前无回归，不替代新 final，也不声称指标提升。

`50/50`、`25/25`、`pass^8=1.0`、终态/state diff 和重复副作用为必须满足的发布/可靠性门禁，不是优展示指标；门禁通过不等于
客服意图准确率或线上推荐收益。Final semantic judge `50/50` 可追溯，但始终是 shadow 信号，不是人工真值、人工准确率或人工一致性。

质量报告不隐藏诊断信号：final 有 `3` 次 query-expansion provider failure，均走安全 deterministic fallback；
regression 有 `1` 次同类诊断和 `1` 次 semantic judge unavailable。它们不会被改写为零，也不会进入不适用的正常质量分母。
独立故障矩阵共 `12` 个场景，其中生产边界 HARD `11/11`、harness boundary SHADOW `1/1`，全部 recovery contract 通过；
它不计入 final 正常质量分母，且 final summary 的 `resilienceMetrics` 仍明确为 `NOT_RUN`。真实隔离 MySQL benchmark 在候选规模
`1/10/50/100` 下验证 batch offer/decision feature 为一次 round trip，而 N+1 随候选数线性增长；100 候选时 batch offer/decision
P50 为 `23.864/2.405 ms`，N+1 为 `89.805/70.501 ms`。Token 只采用 Provider usage；缺 usage 标为
`MISSING_USAGE`，没有可信单价时 `costCny=null`，不写成零成本。官方目录价估算另存为
`ESTIMATED_LIST_PRICE`，带来源 URL、抓取时间、模型 fingerprint 和页面 SHA-256，不能改写运行时状态或启用费用门禁。
所有延迟都是本地完整链路的描述性数据，不是生产 SLO。

只读容量诊断固定 4 条 HUMAN_VERIFIED case，warm-up `4` 次不进分母，正式并发 `1/2/4/8`、每档 `20` 请求；当前 v5 为 `80/80`，QPS `0.396/0.641/0.965/1.353`，c8 混合 P50/P95/P99 `1.052/10.505/12.033s`，LLM 路径 P95 `10.211/9.852/10.574/12.013s`。v5 比 v4 样本更大，但仍受共享本机和外部 Provider 影响。新增单次 LLM hard deadline `45s`，总 Agent/Worker deadline `120s`。纯社交审计探针 `5/5`、P95 `716.1 ms`、Provider calls/token `0`，并在 trace 中确认 `deterministicSocialReply=true`。样本仍只用于瓶颈诊断，不是持续容量或生产 SLO。

历史 `final-20260820-ai-quality-v2` 保留为只读 archive，`v3` 至 `v8` 是只读失败 final archive；它们不代表当前结果，
也不会被删除后重新计算。项目没有 CTR/CVR/GMV、工业级个性化推荐、生产容量或支付合规证据。逐 case、切片、故障、
usage、状态 diff、生命周期和 SHA-256 入口见 [质量评测与 Badcase](docs/evaluation/AI质量评测与Badcase.md)、
[主线与开发记录](docs/project/AI_Shop主线与开发记录.md) 和 [机器清单](docs/evidence-manifest.json)。

---

## 许可证

见 [LICENSE.md](LICENSE.md)
