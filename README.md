# AI-Shop — 受控电商 Agent

> Java 权威交易底座 + Python 受控电商 Agent（AI 导购 + AI 客服）+ RAG/MCP

AI-Shop 把大模型放在电商业务的解释与提案层：Python 负责理解需求、检索知识和调用工具，Java 始终持有商品、SKU、价格、库存、订单、支付及最终写入真相。模型不能直接修改业务库。

当前定位是可复现的本地求职项目，不代表生产容量、真人用户、CSAT、GMV、正式 unseen 或线上 SLO。

## 两条闭环

### AI 导购

```text
自然语言需求
  → BM25 / Vector 并行召回
  → RRF + Rerank
  → Java 商品、SKU、价格、库存快照
  → 推荐卡与理由
  → 点击 / 加购 / 结算归因
  → Java 创建 WAIT_PAYMENT 订单
```

- 预算、品牌、型号、排除词等硬约束由程序校验。
- 推荐结果只能引用 Java 返回的在售商品和当前 SKU。
- 点击、加购和订单项保留同一 `requestId/productId/position/source` 归因链。
- 结算时重新校验权威 SKU、价格和库存，不信任客户端 SKU hash。

### AI 客服

```text
用户问题
  → 意图与风险判断
  → 发布版政策 RAG + Java 订单事实
  → 只读回答 / PROPOSE_* 写操作提案
  → 用户确认
  → Java 重新鉴权、校验状态与幂等执行
  → SUCCEEDED / INCONCLUSIVE / MANUAL_REVIEW
```

- RAG 支持 TXT、Markdown、可抽取文本 PDF/DOCX；扫描件明确拒绝。
- Tool/MCP 只表达调用意图，Java 下游重新验证用户、资源和业务状态。
- 未确认提案不产生副作用；重复确认不重复执行。
- 未授权扣款、账号风险和无法安全收敛的结果转人工。

## 责任边界

| 责任 | 权威组件 |
|---|---|
| 身份、用户、商品、SKU、库存、订单、支付、物流 | Java / MySQL |
| 需求理解、Workflow/Single-Agent 编排、Context | Python / LangGraph |
| 企业政策检索、引用和拒答 | RAG / Elasticsearch |
| 工具协议与调用候选 | MCP |
| 写入确认、幂等、未知结果与人工复核 | Java + Python 状态机 |
| Trace、Token、成本状态、Bad Case | Episode / OTel / Prometheus |

Multi-Agent 与 Text2SQL 默认关闭，只保留为实验代码；视觉找同款是可选能力，不属于主验收。

## 技术栈

- Java 17、Spring Boot、Spring Cloud Alibaba、MyBatis、Seata
- MySQL、Redis、RabbitMQ、Elasticsearch、Sentinel、Nacos
- Python 3.11–3.13、FastAPI、LangGraph、MCP
- Vue 3、TypeScript、Vite、Vitest、Playwright
- OpenTelemetry、Prometheus、Grafana、Loki、Tempo

## 快速启动

要求：JDK 17、Maven 3.9、Python 3.11–3.13、Node.js 22、Docker Compose。

```bash
./start.sh --build
python scripts/bootstrap_demo.py
```

启动脚本会写出 `run/runtime.env`，并依次启动中间件、Java 服务、MCP、Worker、Agent API 和双前端。

访问地址以 `run/runtime.env` 为准，默认用户端为 `http://127.0.0.1:6001`。

停止服务：

```bash
./stop.sh
./stop.sh --middleware
```

详细操作见 [演示指南](docs/demo.md) 和 [架构说明](docs/architecture.md)。

## 验证

```bash
cd AI_Shop-backend
mvn --batch-mode --no-transfer-progress verify
mvn --batch-mode --no-transfer-progress -Pintegration \
  -pl AI_Shop-common,AI_Shop-order/app -am verify

cd AI_Shop-agent
pytest -q
ruff check app tests evaluation

cd ../../AI_Shop-front/AI_Shop-web
npm run lint && npm test && npm run build && npm run test:e2e

cd ../AI_Shop-admin
npm run lint && npm test && npm run build
```

私有 holdout 不属于默认 CI；只有外部恢复后才单独运行：

```bash
pytest -q -m private_holdout
```

当前公开指标、样本量和声明边界见 [评测说明](docs/evaluation.md) 与 [证据清单](docs/evidence/manifest.json)。

## 外部 AI 黑盒试用

Codex、Claude Code、Qwen、DeepSeek 等具备浏览器能力的外部 AI 可以只凭网站和任务卡参加 `SYNTHETIC` 黑盒试用：

```bash
python scripts/blackbox_pilot.py prepare --actor-label <模型> --session 1
python scripts/blackbox_pilot.py finalize --session-id <会话ID>
python scripts/blackbox_pilot.py aggregate --root run/blackbox-pilot
```

外部 AI 不得读取仓库、接口、数据库或预期答案。该结果不是真人试用或 CSAT。

## 仓库结构

```text
AI_Shop-backend/   Java 交易服务与 Python Agent
AI_Shop-front/     用户端与管理端
deploy/            本地中间件、观测和故障演练
docs/              架构、演示、评测、所有权与紧凑证据
scripts/           启动验收与黑盒试用工具
```

完整历史评测包和人工回传不再放在当前展示树，可通过本地归档标签 `archive/pre-career-mainline-20260831` 恢复。

## 已知限制

- 没有真人用户、生产流量、线上 SLO 或支付合规证明。
- WebSocket 断线后可恢复权威终态，但不重放断线期间的 token chunk。
- 当前是单店用户级授权，不是多租户 SaaS。
- PDF/DOCX 只支持可抽取文本，不做 OCR、表格坐标和页码级引用。
- Multi-Agent 未证明优于 Workflow/Single-Agent，因此默认关闭。

项目来源与 AI Coding 边界见 [所有权说明](docs/ownership.md)。许可证见 [LICENSE.md](LICENSE.md)。
