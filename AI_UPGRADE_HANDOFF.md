# AI Shop AI 能力改造 Handoff

更新时间：2026-07-23
当前分支：`dev`
远端仓库：`LittleSongxx/AI_Shop.git`
Git 身份：`song <2212565023@qq.com>`

本文用于交接当前 AI 电商客服、知识库和智能导购改造工作。后续 AI 应在现有唯一实现上继续补齐，不要重新创建第二套客服、RAG 或导购实现。

## 一、当前已完成

### 1. Git 与基础工程

- 已切换并持续在 `dev` 分支开发。
- `origin` 已指向目标仓库。
- README、启动脚本、部署清单已经同步到 Agent API、MCP、Worker 三进程模型。
- Agent 运行环境统一使用 `requirements.lock`；Windows 增加了 `start-worker.bat`。

### 2. AI 客服

- 扩展意图识别：投诉、主动转人工、支付问题、错发/破损、发票、修改地址、退款进度、售后未知等。
- 增加情绪、紧急度、风险等级、下一步动作和转人工原因。
- 已实现：
  - 负面情绪和高风险问题优先转人工；
  - 用户明确要求人工时创建人工会话；
  - 进入人工会话后，后续消息不再进入 AI 队列；
  - 人工客服认领、接入、回复、解决、转回 AI；
  - 用户端人工按钮和人工状态；
  - 用户对 AI 回复点踩/点赞。
- C 端已增加“有用/需改进”反馈入口。
- 管理端 `AgentMessageList.vue` 已包含：
  - 对话记录；
  - 人工会话队列；
  - 会话历史与回复；
  - badcase 列表；
  - badcase 转 FAQ 或忽略。

### 3. FAQ、badcase 与知识库闭环

- 正向反馈会经过保守规则筛选后进入 FAQ 候选池，避免把订单号、退款单、结构化商品卡片等个性化答案直接固化成 FAQ。
- 负向反馈会产生 `ai_badcase_candidate`。
- badcase 支持：
  - `RESOLVED`；
  - `IGNORED`；
  - 由人工修正答案后提交 FAQ 候选。
- 知识库后端已增加：
  - TXT、Markdown、PDF、DOCX 解析；
  - 文本规范化；
  - Markdown/段落标题识别；
  - 约 1200 字符切片和 120 字符重叠；
  - 文档摘要 hash 去重；
  - 文档、切片、入库任务、FAQ 候选、知识发布版本表；
  - 文档上传、发布、归档；
  - FAQ 候选审核；
  - Redis 知识版本广播。
- 管理端 `Rag.vue` 已改为统一知识运营工作台：
  - FAQ 查询、编辑、删除；
  - FAQ 分类、渠道、语言、优先级、生效/失效时间、发布状态；
  - 文档上传、发布、归档；
  - 入库任务进度和失败原因；
  - FAQ 候选审核、答案修订、分类修改、拒绝备注。

### 4. RAG 检索链路

- 已有 ES 关键词检索 + 向量检索 + RRF 合并 + rerank。
- FAQ 增加：
  - 精确问题缓存；
  - FAQ 缓存预热；
  - 知识版本缓存；
  - 发布后 Redis 广播失效；
  - 精确 FAQ 快路径，命中时不调用 LLM；
  - 快路径 1.5 秒超时，知识服务异常不会长时间阻塞客服。
- 精确 FAQ 回复会写入 `source_refs`，包含问题、FAQ ID、来源和知识版本。
- 已增加 RAG 命中/未命中、检索模式、延迟指标。

### 5. 队列、Worker 与运行时健壮性

- Agent 任务从进程内处理改为 RabbitMQ 持久化队列：
  - `agent.support.high`；
  - `agent.faq.fast`；
  - `agent.shopping.low`；
  - `agent.tasks.dead`。
- 高优先级用于投诉、支付、退款、人工等问题；普通商品导购进入低优先级队列；简单 FAQ 进入快队列。
- Worker 已实现：
  - 独立进程；
  - 用户级 Redis 锁；
  - 任务状态表；
  - 发布确认；
  - 重试计数；
  - deadline；
  - 失败恢复；
  - 死信；
  - 心跳；
  - 健康检查 `worker` 字段。
- 已修复一个重要问题：恢复扫描只重新投递 `PENDING/FAILED`，不会把 RabbitMQ 中长时间排队的 `QUEUED` 任务重复发布。
- 已增加 Prometheus 指标：
  - LLM 延迟；
  - RAG 延迟/命中；
  - 熔断状态；
  - 工具调用；
  - Worker 任务发布、开始、完成、重试、死信、锁竞争；
  - Worker 处理中数量；
  - 数据库任务积压量。
- LLM 已支持配置不同的 `LLM_FALLBACK_MODEL`，主模型失败且尚未产生可见流式输出时最多切备用模型重试一次。

## 二、已完成的验证

以下验证在本次交接前已通过：

```text
Simlect-backend/Simlect-agent:
  ruff check app tests        passed
  pytest -q                  102 passed

Simlect-front/Simlect-admin:
  npm run build              passed

Simlect-front/Simlect-web:
  npm run build              passed

Simlect-backend:
  mvn -q -pl Simlect-search,Simlect-admin -am test -DskipTests=false
                              passed
```

已知只是构建警告，不是失败：

- Vite 有既有的 chunk 体积提示；
- pytest 有 LangGraph 依赖弃用提示；
- Maven/Mockito 有 JDK 动态 agent 警告。

目前没有完成真实 Redis、MySQL、RabbitMQ、ES、Nacos、LLM 联调和压测。

## 三、尚未完成的后续任务

下面是被中断前正在分析、但尚未写入代码的智能导购工作，优先级最高。

### P0：智能导购需求理解与约束过滤

当前导购主要依赖：

- 商品关键词/向量混合搜索；
- 当前咨询商品品类；
- 浏览足迹品类；
- 热销兜底。

还没有完整实现以下能力：

1. 从用户表达中结构化提取并保存：
   - 品类；
   - 预算上下限；
   - 偏好品牌；
   - 排除品牌；
   - 使用场景；
   - 核心属性；
   - 禁忌/限制；
   - 是否接受替代品。
2. 将上述偏好按用户隔离后保存到 Redis/会话记忆。
3. 商品召回后进行硬约束过滤：
   - 预算；
   - 品牌；
   - 在售状态；
   - 可售 SKU；
   - 当前咨询商品排除；
   - 不满足约束时不能偷偷回退到无关热销商品。
4. 用户只说“推荐点东西”“买什么好”时，先做需求澄清，不要直接返回热销列表。
5. 用户已经给出“3000 元以内、适合办公、不要某品牌”等约束时，后续追问要继承这些约束。

建议实现位置：

- 新增 `Simlect-backend/Simlect-agent/app/services/shopping_profile_service.py`；
- 在 `AgentOrchestrator.send_message` 中更新用户导购画像；
- 在 `ProductService.search_products` 中读取画像并过滤；
- 为 `product_search` 增加“需要澄清”和“约束未命中”的明确结果来源；
- 不要把个性化结果做成全局缓存。

建议先使用规则抽取预算/品牌/场景，LLM 只做补充，不要让一个不稳定的 LLM 结果直接成为商品硬过滤条件。

### P0：商品实时信息和导购解释

当前商品卡片主要包含 ID、名称、封面和最低价，仍需补充：

- 最高价；
- 品牌；
- 属性；
- 可售 SKU 数量或库存状态；
- 推荐理由。

建议：

1. 扩展 `ProductInfoSnapshotVO`、`ProductSkuSnapshotVO` 或 Agent 商品卡 DTO。
2. Python 侧从 `snapshotBatch` 的属性和 SKU 数据组装品牌、属性和可售状态。
3. 商品过滤不能只依赖旧 RAG 索引；最终结果必须以实时商品服务返回的在售数据为准。
4. `build_product_payload` 增加可选 `reason` 字段，例如：
   - “符合预算”；
   - “匹配办公场景”；
   - “符合偏好品牌”；
   - “同品类替代推荐”。
5. C 端 `AgentProductList.vue` 展示简短推荐理由，但不能展示模型臆造的参数。

### P1：智能导购交互完整性

还可以继续补：

- 明确的“为什么推荐”结构化展示；
- 同类商品对比入口；
- 多候选的价格/属性/销量对比；
- 结果排序的多样性，避免所有结果都来自同一品牌；
- 低置信度时追问，而不是强行推荐；
- 搜索无结果时区分：
  - 没有召回；
  - 有召回但不满足预算；
  - 有召回但已下架；
  - 用户要求的品牌不存在；
- 商品卡曝光、点击、进入详情、加购、购买等事件归因；
- 基于事件的简单离线推荐效果统计。

当前 `ProductController` 已有浏览记录能力，但还没有将 Agent 推荐曝光和点击统一记录为推荐事件。

### P1：RAG 全链路质量闭环

当前精确 FAQ 已有来源记录，但普通混合 RAG 仍有缺口：

1. 让检索器返回结构化 `source_refs`，不要只返回拼接文本。
2. 将来源、命中的 chunk、检索分数、rerank 分数、知识版本写入消息或 trace。
3. 统一记录：
   - 意图；
   - 情绪；
   - 是否转人工；
   - 检索模式；
   - 是否命中；
   - 工具调用；
   - 首 token 延迟；
   - 完整回答延迟；
   - 用户反馈；
   - badcase。
4. 增加离线黄金测试集：
   - FAQ 精确命中；
   - 相似问法；
   - 订单隐私问题；
   - 过期政策；
   - 召回为空；
   - 多文档冲突；
   - prompt injection；
   - 商品事实和库存事实。
5. 增加最小的检索评测命令，至少输出 Recall@K、MRR/Top-K 命中和答案可引用率。
6. 高频问题、正向反馈、人工修正答案要继续进入候选池，但必须保留人工审核和去重。

### P1：客服运营能力

当前人工工作台已经能认领和回复，但还可以继续补：

- 首次响应 SLA 计时；
- 排队等待时长；
- 超时告警；
- 客服并发会话数量；
- 按技能/业务类型分配；
- 客服主动结束话术；
- 人工会话转回 AI 前的上下文摘要；
- 人工回复后的满意度与复盘；
- badcase 按意图、情绪、来源、版本聚合。

项目规模不要求做完整坐席中心，优先做 SLA 和简单统计即可。

### P1：突发流量与部署验证

工程基础已经有队列、熔断和降级，但还没有真实压测。后续应验证：

- RabbitMQ 队列堆积时高优先级是否仍能优先处理；
- FAQ 快路径是否能在 LLM 不可用时正常工作；
- 低优先级导购在 `TASK_QUEUE_MAX` 达到后是否友好降级；
- Java、ES、Embedding、Rerank 单独不可用时的超时和熔断；
- Worker 重启、Rabbit 重连、重复投递、死信恢复；
- Redis/数据库短暂异常时是否会造成用户消息丢失；
- 健康检查和部署脚本是否能识别“API 存活但 Worker 已死”。

建议增加一个轻量压测脚本或至少一组异步集成测试，不必做生产级压测平台。

### P2：安全与数据治理

仍建议复核：

- 管理端知识库接口是否全部经过 Gateway 管理员鉴权；
- FAQ/知识文档上传类型和大小限制；
- 文档中可能出现的手机号、邮箱、订单号是否脱敏；
- 日志和 `source_refs` 是否写入隐私信息；
- 用户消息 prompt injection 与文档 prompt injection；
- 知识库内容是否允许直接覆盖交易规则；
- 退款、支付、物流、库存等事实是否始终以 Java 业务接口为准。

## 四、关键文件索引

### Agent

- `Simlect-backend/Simlect-agent/app/services/agent_service.py`
  - 用户消息入口、精确 FAQ 快路径、队列投递、转人工。
- `Simlect-backend/Simlect-agent/app/services/support_service.py`
  - 人工会话、反馈、badcase、FAQ 候选。
- `Simlect-backend/Simlect-agent/app/services/agent_queue_service.py`
  - RabbitMQ 队列声明和发布。
- `Simlect-backend/Simlect-agent/app/services/task_service.py`
  - 任务状态、积压、恢复。
- `Simlect-backend/Simlect-agent/app/worker.py`
  - Worker、重试、死信、心跳。
- `Simlect-backend/Simlect-agent/app/rag/retriever.py`
  - FAQ 精确缓存、混合检索、RRF、rerank、RAG 指标。
- `Simlect-backend/Simlect-agent/app/graph/nodes.py`
  - LangGraph Agent loop 和备用模型。
- `Simlect-backend/Simlect-agent/app/services/product_service.py`
  - 当前智能导购商品召回和兜底，下一步 P0 改造重点。
- `Simlect-backend/Simlect-agent/app/memory/`
  - 会话摘要、咨询商品、工具结果记忆。

### Search/Java

- `Simlect-backend/Simlect-search/src/main/java/com/simlect/biz/impl/KnowledgeBaseServiceImpl.java`
- `Simlect-backend/Simlect-search/src/main/java/com/simlect/component/KnowledgeDocumentParser.java`
- `Simlect-backend/Simlect-search/src/main/java/com/simlect/component/RabbitMQRagListenerComponent.java`
- `Simlect-backend/Simlect-search/src/main/java/com/simlect/controller/admin/KnowledgeBaseController.java`
- `Simlect-backend/Simlect-search/src/main/java/com/simlect/controller/internal/KnowledgeInternalController.java`

### 管理端/C 端

- `Simlect-front/Simlect-admin/src/views/setting/AgentMessageList.vue`
- `Simlect-front/Simlect-admin/src/views/setting/Rag.vue`
- `Simlect-front/Simlect-admin/src/views/setting/RagEdit.vue`
- `Simlect-front/Simlect-web/src/components/agent/AgentChatItem.vue`
- `Simlect-front/Simlect-web/src/components/agent/AgentSendPanel.vue`
- `Simlect-front/Simlect-web/src/components/agent/AgentProductList.vue`

### 数据库/部署

- `sql/08_search.sql`
- `sql/20260723_ai_shop_upgrade.sql`
- `deploy/GO_LIVE.md`
- `deploy/start-hint.sh`
- `README.md`

## 五、继续开发建议顺序

1. 先实现导购画像、预算/品牌/场景约束和澄清式搜索。
2. 再补商品实时价格/属性/可售状态与推荐理由。
3. 给普通 RAG 回复补结构化来源和离线检索评测。
4. 增加人工 SLA、队列和质量统计。
5. 做一轮真实中间件联调，再决定是否需要扩大改造范围。

## 六、注意事项

- 不要恢复已删除的旧 `RagQuestionController`，当前统一使用 `RagController`。
- 不要重新引入进程内 Agent 任务并发池作为主队列，RabbitMQ + Worker 是当前唯一任务入口。
- 不要把订单、支付、库存、退款结果写死在 RAG 文档或 LLM 回复中。
- 不要把个性化商品结果写入全局缓存。
- 数据库升级脚本需要在目标环境执行；仅编译通过不代表表结构已经升级。
- 后续每次改造至少执行：

```bash
cd Simlect-backend/Simlect-agent
.venv/bin/python -m ruff check app tests
.venv/bin/python -m pytest -q

cd ../../Simlect-front/Simlect-admin
npm run build

cd ../../Simlect-front/Simlect-web
npm run build

cd ../../Simlect-backend
mvn -q -pl Simlect-search,Simlect-admin -am test -DskipTests=false
```
