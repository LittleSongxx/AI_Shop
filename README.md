# AI_Shop — AI 驱动的微服务电商平台

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
| RAG | 向量检索（pgvector/ES）+ BM25 混合 |
| 工具调用 | MCP（Model Context Protocol）双向通信 |
| 会话记忆 | Redis 短期 + MySQL 长期持久化 |
| 可观测 | OpenTelemetry（OTLP）+ Prometheus 指标 |
| 测试 | pytest（363 用例：362 通过 / 1 跳过，以 CI 为准） |

### 前端

| 模块 | 技术选型 |
|------|---------|
| 用户端 | Vue 3 + Vite + Element Plus |
| 管理后台 | Vue 3 + Vite + Element Plus |

### 基础设施

Docker Compose：MySQL 8 · Redis 7 · RabbitMQ 3 · Nacos 2 · Elasticsearch 9 · Sentinel · Seata 2

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
├── AI_Shop-search/               # ES 索引构建脚本
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
- **签到系统**：Bitmap 月度记录 + Hash 计数器 + Lua 原子操作，补签次数按累计天数兑换
- **敏感词过滤**：DFA 算法，支持管理员动态维护词库

### AI 购物导购与客服

- **ReAct Agent**：基于 LangGraph 的有状态对话，支持商品推荐、订单查询、物流查询、售后引导
- **RAG 知识库**：店铺公告、商品详情、退换货政策向量化，混合检索后注入上下文
- **MCP 工具链**：结构化工具调用（查询订单/物流/券/商品），结果以卡片形式渲染至前端
- **输入防护**：NFKC 归一化 + 两级规则（硬阻断 / 可疑累积）+ Propose→用户确认→Java 执行，防 Prompt 注入
- **强制工具回退**：模型跳过必要工具时，框架层自动补全并路由至 finalize，避免幻觉回复
- **熔断降级**：Circuit Breaker 包装外部 LLM 调用，超时自动降级
- **速率限制**：用户级别双窗口限流，防止滥用

---

## 快速启动

### 前置依赖

- JDK 21+、Maven 3.9+
- Python 3.11–3.12
- Docker & Docker Compose

### 0. 一键起停（推荐）

```bash
./start.sh --build          # 首次：Maven 打包 + 中间件 + Java 微服务 + Agent
./start.sh                  # 后续：跳过构建
./start.sh --middleware-only  # 只起 Docker 中间件
./stop.sh                   # 停 Java + Agent（中间件保留）
./stop.sh --middleware      # 一并停中间件
```

`start.sh` 会等待 MySQL / Nacos / Seata 就绪再拉起 Java 服务，PID 和日志落在 `run/`。
Agent 走 conda 环境 `shop`。下面 1–5 步是拆开的手工流程，排查问题时用。

### 1. 启动中间件

```bash
cd deploy
docker compose -f docker-compose.middleware.yml up -d
```

等待 Nacos（8848）、MySQL（3306）、Redis（**6380**）、RabbitMQ（**5673**）、ES（9200）全部就绪。

> Redis / RabbitMQ / Seata 的宿主机端口是 +1 偏移（6380 / 5673 / 8092），避免和本机已装的同类服务冲突。
> 配置里的默认值已经是偏移后的端口，所以不传环境变量也能连上；手工起服务时唯一必须给的是
> `MYSQL_PASSWORD`。详见 [deploy/MIDDLEWARE_DOCKER.md](deploy/MIDDLEWARE_DOCKER.md)。

### 2. 初始化数据库

```bash
mysql -h 127.0.0.1 -u root -p < sql/init.sql
```

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
| `AISHOP_PRODUCTION_READY` | 设为 `true` 才允许启动生产实例，触发完整安全校验 |
| `AISHOP_DEV_LOGIN_BYPASS` | 本地调试开关，**禁止生产开启** |
| `LLM_BASE_URL` | LLM API 基础地址（OpenAI 兼容） |
| `LLM_API_KEY` | LLM API Key |
| `LLM_MODEL` | 模型名称，如 `claude-opus-5` |

完整清单见 [AI_Shop-backend/AI_Shop-agent/.env.example](AI_Shop-backend/AI_Shop-agent/.env.example) 和 [deploy/env.production.example](deploy/env.production.example)。

---

## 数据与指标口径

仓库里的数字分三类：实测的、框架就绪但没数据的、合成或手工编写的。引用任何指标前先看
[docs/DATA_PROVENANCE.md](docs/DATA_PROVENANCE.md)，评测集的已知失败见
[benchmarks/KNOWN_LIMITATIONS.md](AI_Shop-backend/AI_Shop-agent/benchmarks/KNOWN_LIMITATIONS.md)。

---

## 许可证

见 [LICENSE.md](LICENSE.md)
