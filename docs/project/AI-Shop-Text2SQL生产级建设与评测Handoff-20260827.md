# AI-Shop Text2SQL 生产级建设与评测 Handoff

> 快照日期：2026-08-27（Asia/Shanghai）
>
> Git 基线：`4d3dd0b3c61ebace8354b791e95c89584d563073`
>
> 当前分支：`dev`，快照时与 `main`、`origin/dev`、`origin/main` 指向同一提交
>
> Python 环境：Conda `shop`
>
> 本文将需求中偶尔出现的 “Test2Sql” 统一写作 **Text2SQL**。

本文是交给后续 AI/开发者的工作底稿，不是不可修改的最终方案。它刻意区分四类内容：

- **仓库事实**：可以直接从当前代码、Git 或已归档证据复核。
- **当前判断**：基于现状得出的风险或缺口判断，接手者应复核。
- **建议起点**：用于减少重复排查，不要求机械照做。
- **开放决策**：需要结合新调研、真实数据、业务负责人意见和基线结果重新决定。

接手者可以改变技术路线、数据规模、指标门槛和实施顺序，但应保留事实证据、说明为什么改变，并避免把尚未验证的能力写成既成质量结论。

## 1. 一页结论

AI-Shop 当前真正成熟的质量证据主要集中在商品搜索/推荐和客服 Agent 两条主线。Text2SQL **并非只有概念或残留代码**：仓库里已经存在面向管理端的数据分析页面、Java 权限入口、Python 分析服务、十个治理视图、SQL AST 防护、只读数据库身份、查询预算、结果游标、异步导出和审计记录。

但它目前更准确的定位仍是：

> 一个安全治理骨架较完整、业务正确性尚未被系统评测证明的管理端分析实验。

已有测试能够证明许多拒绝规则、权限边界和确定性辅助逻辑按代码预期工作；它们不能证明自然语言问题被正确理解，也不能证明 SQL、查询结果和最终文字答案符合电商业务口径。当前没有与客服评测包同等级的 Text2SQL 人工金标集、冻结数据库快照、端到端评测 runner、分层 badcase 或 unseen holdout。因此不能继承其他模块的 `120/120` 等质量结论，也不能声称 Text2SQL 已达到生产准确率门槛。

后续最有价值的工作不是先继续堆 prompt，而是先把“指标定义—语义计划—SQL—数据库状态—结果—最终回答”做成可复核合同，并建立能真正暴露安全但错误 SQL 的评测闭环。当前代码规模和固定语义视图很适合探索“LLM 负责语义选择、确定性代码负责编译和校验”的路线；这只是优先验证的假设，不是必须采用的架构。

## 2. 接手时的仓库真实状态

### 2.1 Git 与工作区

快照时：

- 本地仅有 `main`、`dev` 两个分支，远端也仅有 `origin/main`、`origin/dev`。
- 四个引用都指向提交 `4d3dd0b`，`origin/HEAD` 指向 `origin/main`。
- 编写本文前工作区干净；本文及文档索引的变更是此提交之后的新工作区变更。
- 本地仍有两个历史 stash：
  - `stash@{0}`：`pre-branch-consolidation-untracked-20260827`
  - `stash@{1}`：旧 `ai-app-agent-quality` 工作区
- stash 不是 `dev` 当前内容，也没有被远端分支承载。除非先完成路径、敏感信息和重复内容审查，不要随意 apply、drop 或把它们混入 Text2SQL 基线。

开始工作时应重新执行 Git 检查；本文只描述快照，不保证后续没有新提交或用户修改。

### 2.2 项目能力边界

当前项目可以概括为三块：

| 能力 | 当前实际定位 | 已有证据边界 |
|---|---|---|
| 商品搜索/推荐 | 电商约束搜索、候选排序、权威商品/库存/报价快照 | 有离线回归与历史 final，但不等于线上转化或任意商品规模能力 |
| 客服 Agent | 意图/槽位、RAG/权威工具、确认写入、转人工和审计 | 有多轮人工审批回归；最新高分来自已见开发集，不是 unseen 或线上成功率 |
| 管理端 Text2SQL | 只读业务分析、澄清、表格/图表/解释、导出 | 有治理和单元测试；尚无可信的端到端业务正确性基线 |

现有 README 将 Text2SQL 描述为“治理实验”。这一定位是合理的。README 中“扫描预算”一词需要谨慎理解：当前实现有时间、行数、日期范围、结果字节等预算并保存 `EXPLAIN`，但尚未看到按预估扫描行数或查询成本进行硬拒绝的完整门禁。

## 3. Text2SQL 当前已经有什么

### 3.1 用户入口与调用链

管理端页面位于 [DataAnalyst.vue](../../AI_Shop-front/AI_Shop-admin/src/views/data/DataAnalyst.vue)，目前支持：

- 输入单个自然语言分析问题；
- 在特定歧义场景下展示澄清选项；
- 展示文字结论、表格、图表和部分 lineage/SQL/trace；
- 异步发起并下载导出；
- 另有库存建议模式，但不要自动把它与通用 Text2SQL 的准确率合并。

Java 管理端入口位于 [AgentMessageController.java](../../AI_Shop-backend/AI_Shop-admin/src/main/java/com/aishop/controller/admin/AgentMessageController.java)：

- 分析请求需要 `ANALYTICS_READ`；
- 导出相关请求需要 `ANALYTICS_EXPORT`；
- Java 将管理端身份与权限以签名断言传给 Agent，而不是接受浏览器自行声明权限。

Python HTTP 路由位于 [agent.py](../../AI_Shop-backend/AI_Shop-agent/app/api/routes/agent.py)，包含管理端问答、导出、状态查询和下载接口。核心链路可以概括为：

```text
管理员问题 + 已验证身份/权限
  -> 识别是否需要澄清
  -> 生成结构化分析计划（最多三个分析分支）
  -> 为每个分支生成 SQL 草稿
  -> SQL AST/权限/目录/预算校验
  -> EXPLAIN + 只读查询
  -> 确定性数值叙述、表格与图表
  -> 审计 Episode / 游标 / 可选导出
```

核心编排在 [data_analyst_service.py](../../AI_Shop-backend/AI_Shop-agent/app/services/data_analyst_service.py)。当前已有的设计亮点包括：

- 计划、分支和 SQL 草稿使用结构化对象，而不是完全依赖自由文本；
- 模型输出要求结构化 JSON，失败时有有限重试/修复；
- 多指标问题可拆成最多三个分支并行执行；
- 查询后的核心数值描述由代码生成，降低模型随意改写数字的概率；
- 结果大小、游标和审计信息有明确处理；
- 对因果性表述保持保守，不把简单相关结果直接包装成因果结论。

### 3.2 治理语义目录

[analytics_catalog.py](../../AI_Shop-backend/AI_Shop-agent/app/services/analytics_catalog.py) 当前登记十个可查询视图：

| 视图 | 预期分析主题 | 接手时特别需要复核的业务语义 |
|---|---|---|
| `analytics_sales_daily` | 每日销售汇总 | 销售、退款、净额和日期归属 |
| `analytics_product_sales_daily` | 商品维度每日销量 | 订单数、件数、金额、商品快照和退款口径 |
| `analytics_inventory_risk` | 库存风险 | 可售库存、阈值、缺货和滞销定义 |
| `analytics_agent_quality_daily` | Agent 质量 | 指标分母、版本和采样范围 |
| `analytics_tool_quality_daily` | 工具质量 | 成功、业务拒绝、技术失败的区别 |
| `analytics_recommendation_funnel_daily` | 推荐漏斗 | 曝光、点击、加购、支付是否按同一 cohort 归因 |
| `analytics_recommendation_quality_daily` | 推荐质量 | 离线指标和线上事件口径是否混用 |
| `analytics_offer_quality_daily` | 报价质量 | 价格、优惠、可用性与时间快照 |
| `analytics_fulfillment_after_sales_daily` | 履约/售后 | 创建日、发货日、完成日、退款日的事件归属 |
| `analytics_inventory_forecast` | 库存预测 | 预测方法、置信度含义和可操作性边界 |

这些视图的 DDL 在 [R__current_schema.sql](../../AI_Shop-backend/AI_Shop-admin/src/main/resources/db/migration/R__current_schema.sql)。它们使用 PII-free 的语义视图和 `SQL SECURITY DEFINER`，这是一个良好的治理起点，但“视图存在”不等于“业务定义已经由负责人签字确认”。

### 3.3 SQL 防护与数据库隔离

[sql_guard.py](../../AI_Shop-backend/AI_Shop-agent/app/services/sql_guard.py) 使用 SQLGlot AST，而不是仅用正则字符串过滤。当前主要边界包括：

- 只允许 `SELECT`；
- 只允许一个目录内视图和允许列/函数；
- 拒绝写操作、`*`、普通 join、子查询、锁、变量、跨库引用和 offset；
- 要求 `LIMIT`，对带日期的视图要求受限日期范围；
- 将权限、可见视图、列策略和 tenant scope 纳入 fail-closed 校验；
- 允许受控的非递归单 CTE。

数据库连接在 [analytics_pool.py](../../AI_Shop-backend/AI_Shop-agent/app/db/analytics_pool.py)：

- 使用专用分析凭据；
- 拒绝 root/业务服务身份；
- 要求连接 `aishop_admin` 并设置只读事务；
- 配套脚本 [provision-analytics-reader.sh](../../deploy/provision-analytics-reader.sh) 仅授予十个视图的 `SELECT`。

这些设计显著减少破坏性 SQL 和越权面，但 AST 合法只证明“语法和资源边界内可执行”，不能证明“准确回答了用户的问题”。目前 guard 没有完整验证 SQL 中的指标表达式、聚合方式、过滤条件、排序方向和计划完全等价。

### 3.4 预算、结果和导出

配置集中在 [settings.py](../../AI_Shop-backend/AI_Shop-agent/app/config/settings.py)、`.env.example` 和 `deploy/env.production.example`。快照中的典型默认值包括：

- 查询最大行数约 `200`；
- 日期范围最大约 `90` 天；
- 查询超时约 `3000 ms`；
- 模型、请求、结果字节、游标和导出均有独立限制。

[analytics_export_service.py](../../AI_Shop-backend/AI_Shop-agent/app/services/analytics_export_service.py) 已实现异步、所有者绑定、审计和重启恢复。不过当前问答路径会把查询结果钳制到约 `200` 行，而导出配置允许更大的约 `10,000` 行；因此“大导出”配置不代表实际已经能导出 10,000 行。接手者需要决定导出应复用已冻结结果、重新运行专用导出查询，还是保持小结果导出，并建立相应快照一致性和资源门禁。

## 4. 当前到底证明了什么

### 4.1 已验证的代码级证据

快照前在 Conda `shop` 环境执行过：

```bash
cd AI_Shop-backend/AI_Shop-agent
conda run --no-capture-output -n shop pytest -q \
  tests/test_data_analyst_service.py \
  tests/test_analytics_governance.py
```

结果为 `23 passed`。另有两个管理端分析/SQL guard 相关的多 Agent harness 定向测试通过。相关测试文件为：

- [test_data_analyst_service.py](../../AI_Shop-backend/AI_Shop-agent/tests/test_data_analyst_service.py)
- [test_analytics_governance.py](../../AI_Shop-backend/AI_Shop-agent/tests/test_analytics_governance.py)
- [test_multi_agent_harness.py](../../AI_Shop-backend/AI_Shop-agent/tests/test_multi_agent_harness.py)

它们主要证明代码分支、权限、SQL 拒绝规则、计划/结果辅助逻辑等按测试预期工作，是重要的工程回归证据。

### 4.2 尚未证明的质量结论

当前仓库没有发现完整的 Text2SQL 专项评测集和证据包，因而以下说法都还没有足够证据：

- “自然语言到 SQL 准确率达到某个百分比”；
- “金额、退款、库存、履约等关键指标回答正确”；
- “对歧义问题能够稳定澄清，对不可回答问题能够稳定拒答”；
- “跨多种数据库状态仍能得到语义正确结果”；
- “达到生产 p95/p99、扫描成本或并发容量目标”；
- “中文改写、错别字、同义词、时间表达和实体名称具有足够鲁棒性”；
- “公开 Text2SQL benchmark 的成绩等同于本项目业务质量”。

尤其不要用以下替代物证明 Text2SQL 正确性：

- 单元测试通过率；
- SQL guard 拒绝率；
- 查询可执行率；
- JSON 格式成功率；
- 客服或商品搜索已有的人工质量分数；
- 模型自己对答案的打分。

## 5. 优先复核的风险与未知项

下面是接手者的调查清单，不应在未复核前全部当作确定 bug。

### 5.1 业务语义与 SQL 正确性

1. **计划到 SQL 的约束仍偏软。** 当前 prompt 可以要求指标和派生表达式，但确定性 guard 主要验证安全结构，未完整证明 SQL 使用了正确 metric、aggregation、filter、group、order、limit。
2. **同一结果不一定代表同一语义。** 在一个小数据库状态上，错误的 `COUNT(order_id)`、`SUM(quantity)`、gross/net sales 或 `>`/`>=` 可能碰巧相等；评测需要多个能区分这些错误的数据库状态。
3. **销售/退款日期口径需业务审查。** 当前视图可能把订单当前状态归到下单日、退款归到完成日；这是否符合“某天销售额/退款额”的管理口径，需要产品、财务和数据负责人共同确认。
4. **履约视图的事件日期需确认。** 按订单创建日聚合当前状态，不一定等于“当天发货/当天完成”的事件流指标。
5. **推荐漏斗需确认 cohort。** 曝光、点击、加购、支付按各自发生日相加，可能不同于以曝光 cohort 计算转化率。
6. **库存预测的 confidence 是什么。** 如果只是观测天数比例或启发式评分，不能把它写成经过校准的统计置信概率。
7. **单位、币种、时区、退款符号和小数精度。** 金额如果在结果归一化中转成 float，财务场景可能产生不必要的精度和展示风险。

### 5.2 对话与可回答性

- 目前确定性日期解析重点覆盖“最近 N 天”，其他中文日期表达更多依赖模型。
- 明确的“最好卖”等措辞已有特定澄清规则，但一般性的指标歧义、时间歧义、维度歧义和实体歧义尚未形成系统合同。
- 缺少通用的商品/类目/品牌实体解析与候选消歧层。
- 多轮对话状态主要围绕当前澄清交互，尚不能默认它已支持复杂追问、省略指代和条件修改。
- 对目录外问题、数据库中不可证明的问题和要求预测/因果解释的问题，需要区分 `CLARIFY`、`ABSTAIN`、`DENY` 与“允许回答但加边界”。

### 5.3 一致性、资源与运行时

- 多分支查询没有明确共享同一个 `dataAsOf` 或数据库快照；快速变化的数据可能产生彼此矛盾的分支结果。
- 游标翻页可能重新规划、重新生成和重新执行 SQL，结果未必是同一快照的稳定后续页。
- `EXPLAIN` 被记录，但尚无明确的预估扫描行数/成本硬门禁。
- 分支部分失败时可能整体呈现 `SUCCEEDED` 并附 warning；需要确认 API、前端和用户对“部分成功”的理解一致。
- 数据库启动探针似乎只检查目录前五个视图，而非全部十个，需确认是否会让后五个视图的问题延迟到用户请求时才暴露。
- 尚缺大数据量、并发、慢查询、连接池耗尽、模型超时、数据库切换和服务重启下的专项证据。

### 5.4 评测与证据链

- 没有冻结的业务数据库快照或可重建 fixture 版本。
- 没有人工确认的指标目录、问题意图、gold plan、gold SQL/结果 oracle。
- 没有将 SQL 结果与最终自然语言事实逐项对齐的 scorer。
- 没有按金额/退款/库存等高风险切片报告结果。
- 没有鲁棒性族、攻击集、权限矩阵、schema/metric drift 和重复运行稳定性评测。
- 没有把开发集、verified-query 示例、回归集和 sealed holdout 隔离。

## 6. 候选目标架构：先验证，再决定

当前固定十个治理视图、有限维度和明确管理端边界，使下面的架构成为值得优先实验的起点：

```text
身份与权限
  -> 可回答性 / 歧义识别
  -> 结构化语义计划
     {metric, dimensions, filters, time_range, grain, order, limit}
  -> 确定性 SQL 编译或强约束生成
  -> AST 安全校验 + 语义计划一致性校验
  -> EXPLAIN 资源门禁
  -> 只读副本/冻结快照执行
  -> 结果形状与单位校验
  -> 只基于结果和 lineage 的叙述/图表
  -> 审计与反馈
```

接手者至少可以比较三条路线：

| 路线 | 可能优势 | 主要代价/风险 |
|---|---|---|
| A. 语义计划 + 确定性 SQL 编译器 | 对固定目录可解释、稳定、易做 plan-SQL 等价校验 | 编译规则和业务目录维护成本较高，长尾表达扩展较慢 |
| B. 约束式 LLM 生成 SQL + 语义 verifier | 保留长尾灵活性，改造现有代码较小 | verifier 难以覆盖所有隐蔽语义错误，provider 波动更明显 |
| C. verified query/模板检索 + 编译器或 LLM fallback | 高频问题稳定，可利用已人工确认查询 | 示例污染 holdout、模板陈旧和 fallback 边界需要治理 |

一个合理的实验顺序是先做小型评测基线，再用相同数据比较 A/B/C 或混合方案。若结果显示现有自由 SQL 生成在受控目录内已经足够准确，也可以保留并重点加强 semantic verifier；若确定性编译覆盖率更高，则可让 LLM 只选择语义对象。不要为了“架构更先进”在没有评测的情况下重写全部服务。

默认建议继续保持 **只读管理分析**。如果未来要让自然语言直接修改价格、库存或订单，那应被视为新的高风险 action product，另行设计确认、审批、幂等、回滚和审计，而不是把公开 benchmark 的 CRUD 能力直接接入当前入口。

## 7. 建议的评测问题与指标

### 7.1 先定义一次请求可能有哪些正确结果

评测合同不应假定每个问题都必须生成 SQL。建议允许以下 outcome，具体枚举名可调整：

- `ANSWER`：问题可由当前授权语义层回答，应得到正确结果和陈述。
- `CLARIFY`：信息不足或存在会改变答案的多种合理解释，应提出最小必要澄清。
- `ABSTAIN`：当前数据/指标无法支持，应明确说明能力边界。
- `DENY`：权限不足、攻击或策略禁止，应拒绝且不泄露信息。

这会比只统计“执行成功率”更接近真实企业使用场景。

### 7.2 建议的核心指标

以下指标是候选集合，接手者可以根据业务风险删减或调整定义。

| 维度 | 建议指标 | 要回答的问题 |
|---|---|---|
| 端到端可信度 | **Trusted Answer Rate**（建议自定义北极星） | 正确结果、正确最终事实、正确权限且资源合规的请求占比 |
| 查询正确性 | Denotation/Test-suite Accuracy | SQL 在一个或多个区分性数据库状态上的结果是否符合 oracle |
| 语义计划 | metric/dimension/filter/time/grain exact 或字段级 F1 | 系统是否正确理解业务问题，即使后续 SQL 失败 |
| 安全覆盖 | Safe Coverage | 系统能正确回答的比例；不能靠大量拒答获得虚高准确率 |
| 澄清/拒答 | precision、recall、macro-F1、overconfident-wrong rate | 该问时是否问、该拒时是否拒、可答时是否误拒 |
| 最终回答 | required-fact accuracy、单位/币种正确率、groundedness | 结果对但文字是否改错数字、扩大结论或遗漏边界 |
| 权限安全 | 严重越权/泄漏/写入/跨租户事件 | 防护在攻击和权限矩阵下是否失效 |
| 鲁棒性 | paraphrase consistency、扰动后准确率下降 | 中文改写、错别字、顺序变化、无关语句是否改变语义 |
| 稳定性 | 同一请求重复运行一致率 | provider 随机性是否导致计划、结果或 outcome 漂移 |
| 效率 | p50/p95/p99、DB 时间、扫描量、token、调用数、费用 | 达到可信结果所需的真实资源和延迟 |
| 回归 | 新增/重开 badcase 数，关键切片退化 | 优化是否以牺牲另一类问题为代价 |

`Trusted Answer Rate` 是建议的项目指标，不是行业统一名称。一个可讨论的定义是：

```text
可信回答 = outcome 正确
        AND（ANSWER 时结果 oracle 通过）
        AND 最终陈述 required facts/单位通过
        AND 无权限或数据泄漏
        AND 未超过预先定义的资源预算
```

不要只报一个综合分。金额、退款、库存、履约、权限攻击和不可回答问题应单独给出分母、置信区间和 badcase；任何严重越权也不应被平均分掩盖。

### 7.3 可供讨论的初始门槛

在没有基线前不宜把数字写成永久 release gate。以下只能作为首轮评审的量级参考：

- 整体 Trusted Answer Rate 约 `>= 90%`；
- 金额/退款/库存等关键切片试点约 `>= 95%`，扩大上线前可讨论 `>= 98%`；
- 观察到的严重越权、跨租户泄漏和写入为 `0`；
- overconfident wrong 约 `<= 1%`；
- 澄清/拒答 macro-F1 约 `>= 90%`；
- 鲁棒性相对下降约不超过 `5` 个百分点；
- 简单查询 p95 可先以 `8s`、多分支以 `15s` 作为调查线，而不是直接承诺生产 SLO。

接手者应先跑出现状基线，再结合真实用户容忍度、数据库规模、provider、部署环境和错误成本校准门槛。零样本的小分母 `0` 次安全事故只能写作“本评测未观察到”，不能声称绝对安全。

### 7.4 Scorer 的优先级

建议按以下可信度顺序组织：

1. 结果集合/标量和单位的确定性比较；
2. 在多个数据库状态上的 test-suite comparison；
3. 结构化 plan 与 SQL AST 的一致性检查；
4. 最终答案中的必需事实、禁止断言和数字绑定；
5. 人工复核；
6. 经人工校准、固定模型/提示/版本的 LLM judge，作为辅助而非唯一裁判。

Exact SQL string match 只适合诊断，因为同一问题可以有多个正确 SQL；反过来，单一数据库上的相同结果也可能只是碰巧相等。

## 8. 评测集如何构建

### 8.1 建议先做可工作的 v0

为了尽快得到真实基线，可以先构建约 `250` 条业务问题和 `100` 条安全/权限问题的 v0。这个数量只是工作量参考：如果高质量人工资源有限，宁可先做更小但具有区分性数据库状态和可靠标注的集合，也不要堆大量模型自生成同质题。

覆盖建议包括：

- 十个治理视图全部覆盖；
- 单指标、分组、Top/Bottom、趋势、环比/同比（若语义层确实支持）；
- 多指标和多分支问题；
- 金额、订单数、商品件数、退款、库存、履约、推荐漏斗；
- 时间边界、时区、自然月、最近 N 天、空区间；
- 商品、类目、品牌的同名、近似名、已改名和不存在实体；
- 歧义、信息不足、目录外、预测、因果和主观建议；
- 无权限、部分权限、跨租户、提示注入、SQL 注入和资源消耗攻击；
- 空结果、NULL、零值、并列、重复、全额/部分退款、超过 200 行；
- 中文同义改写、错别字、口语、省略、无关前后缀和否定表达。

v0 暴露并用于修复后，可再建立大约 `500–600` 条业务题、`200` 条安全题，以及 `100` 个鲁棒性 family（每族 3–5 个变体）的 v1。规模应由 badcase 饱和度和切片置信区间决定，而不是把这里的数字当作硬任务。

### 8.2 每条样本建议保存的字段

可以从下面的逻辑 schema 起步，字段名允许调整：

```json
{
  "case_id": "t2sql-sales-001",
  "question": "最近30天销售额最高的5个商品是什么？",
  "permissions": ["ANALYTICS_READ"],
  "tenant_id": null,
  "snapshot_id": "fixture-v1-state-a",
  "catalog_version": "analytics-catalog-v1",
  "expected_outcome": "ANSWER",
  "gold_plan": {
    "metrics": ["net_sales_amount"],
    "dimensions": ["product"],
    "time_range": "fixture_relative_last_30_days",
    "order": [{"field": "net_sales_amount", "direction": "DESC"}],
    "limit": 5
  },
  "gold_sql": ["one-or-more-reviewed-equivalent-queries"],
  "result_oracle": "typed expected rows or deterministic oracle reference",
  "order_sensitive": true,
  "numeric_tolerance": "explicit decimal policy",
  "required_facts": ["product_name", "net_sales_amount", "date_range"],
  "forbidden_claims": ["causal claim", "forecast claim"],
  "resource_budget": {"max_rows": 200, "max_estimated_scan": "TBD"},
  "slice_tags": ["sales", "money", "topk", "critical"],
  "annotation": {"author": "TBD", "reviewer": "TBD", "adjudication": null}
}
```

日期最好锚定 fixture 的固定时钟，不要让“最近 30 天”随运行日漂移。金额应使用 Decimal/最小货币单位或明确 tolerance，不要默认 float 近似就是正确。

### 8.3 数据库状态比问题数量更重要

至少准备若干可重建的数据库状态，使常见错误 SQL 产生不同结果。例如：

- 订单数与商品件数不同；
- gross sales 与退款后 net sales 不同；
- 全额和部分退款跨越日期边界；
- `>` 与 `>=` 在阈值边界不同；
- 当前状态与状态事件发生日不同；
- 两个商品并列但稳定排序规则可验证；
- 同一名称在不同 tenant 或历史快照中不同；
- 漏斗按事件日和曝光 cohort 计算得到不同结果。

可以从人工 gold SQL 生成邻近 mutation（换聚合、漏 filter、错状态、错日期、错排序），再检查 fixture 是否能“杀死”这些 mutation。这样比只看一个开发数据库的结果更能发现安全但语义错误的 SQL。

### 8.4 标注和仲裁

推荐的责任分工，而非强制人员配置：

- 业务/数据负责人确认 metric、维度、时间和可回答性；
- SQL/数据分析人员给出 gold plan、等价 SQL 和结果 oracle；
- 第二位 reviewer 独立检查；
- 分歧由业务负责人、DBA 或资深分析人员仲裁；
- 高风险财务/退款/库存用例应有对应领域责任人确认。

允许 AI 辅助生成问题改写、候选 SQL、文字说明和文件落盘，但最终决策应由人工拥有。沿用本项目既有边界时，可以记录为 `HUMAN_APPROVED_AI_ASSISTED`、`humanDecisionAuthority=true`、`aiAssistanceUsed=true`，不要声称 `pureHumanUnaided`。

每次封存应保存数据集、review sheet、仲裁、数据库快照/构建脚本、catalog、代码、prompt/provider/model、配置、报告、badcase 和 SHA-256。失败结果应进入 archive，不要为了得到漂亮 current 指标而删除。

### 8.5 切分与防污染

- 不要随机按单行拆分高度相似改写；同一 scenario、SQL skeleton、metric/entity 和 paraphrase family 应整体落在同一 split。
- verified-query 示例和 few-shot prompt 素材不能与 sealed holdout 重叠。
- development、regression、public benchmark、internal holdout、online shadow 要分开报告。
- holdout 最好由非日常开发者保管；每次正式版本只按预注册规则运行，避免反复看答案调 prompt。
- schema 或 metric 定义变化时，生成新 catalog/dataset 版本并说明兼容性，不原地改 gold。

## 9. 公开评测集怎么用

公开 benchmark 可以衡量通用 Text2SQL 引擎能力，但不能替代 AI-Shop 内部业务评测。当前生产 guard 只允许十个治理视图且限制 join/subquery，无法不加适配地运行大多数公开集；应在隔离 sandbox 中建立 benchmark adapter，绝不能使用生产凭据或放宽生产入口来迁就榜单。

可重新调研的起点：

- **BIRD Mini-Dev / BIRD**：较适合先验证执行准确率、复杂 SQL 和数据库值理解；Mini-Dev 的 MySQL 子集便于当前栈接入。
- **Falcon**：包含中文企业/电商类问题，可用于中文能力参考，但其 MaxCompute/Hive 语法与当前 MySQL 需要适配。
- **Spider 2.0 / LiveSQLBench**：更复杂、更接近真实数据库工作流，可用于宽能力诊断。
- **BIRD-Interact / CHASE**：只有在项目认真建设多轮澄清和追问后再纳入更有意义。
- **Dr.Spider / TrustSQL / SecureSQL 类集合**：适合补鲁棒性、信任和攻击维度。

报告中应分开写：

1. `AI-Shop product Text2SQL quality`：内部语义层、权限和业务数据库上的质量；
2. `Generic Text2SQL capability`：公开 benchmark 的隔离实验结果；
3. `Governance/security diagnostics`：拒绝、权限、资源和攻击专项。

公开榜单高分不证明 AI-Shop 的销售额口径正确，内部十个视图高分也不证明系统能处理任意企业数据库。

## 10. 可调整的推进路线

### 阶段 0：重新确认基线和范围

建议产物：

- 当前源码、配置、provider、数据库、目录版本和已知限制快照；
- 十个视图的 owner、metric dictionary、日期/币种/时区说明；
- 当前实现端到端运行的少量 trace；
- “只读分析是否仍是产品范围”的确认记录。

接手者应先重新联网调研 2026 年仍活跃的企业 Text2SQL 方案和 benchmark，验证本文外部资料是否过时。

### 阶段 1：语义合同与可重建 oracle

建议产物：

- versioned analytics catalog；
- 指标、维度、可组合性、权限和 answerability 合同；
- 具有区分性的 fixture/snapshot；
- 首批人工审核 gold cases。

这里可能会暴露视图本身的问题。应先修业务定义再大量生产 gold，避免为错误语义层建立精致评测。

### 阶段 2：先建设评测 runner

建议 runner 能：

- 启动或校验隔离数据库状态；
- 通过真实管理端 Agent 路径执行，而非只调用内部函数；
- 捕获 plan、SQL、guard、EXPLAIN、结果、叙述、权限和 trace；
- 运行确定性 scorer 和可选人工/LLM 辅助 scorer；
- 按切片生成 summary、case、badcase、配置和 hash；
- 重复运行并比较 provider/seed 稳定性；
- 将失败 evidence 保持不可变。

目录可沿用现有 `evaluation/` 与 `evaluation-evidence/` 体系，也可以在确认现有 CLI 扩展成本后建立独立 `text2sql` 子模块。

### 阶段 3：跑现状真实基线并分类 badcase

在不先改核心 prompt/架构的情况下跑 v0，建立：

- outcome、plan、execution、denotation、narrative、security 各层漏斗；
- 按 view、metric、风险、复杂度和语言扰动的结果；
- 至少 3–5 次重复运行稳定性；
- provider 调用、token、费用、DB 时间、扫描量和端到端延迟；
- 可复现的 badcase taxonomy。

这一阶段的结果用于决定主要瓶颈究竟在语义层、计划、SQL、数据库、回答生成还是运行时，不要预设所有错误都来自模型。

### 阶段 4：用证据选择并加固架构

可并行做小型 A/B：确定性 compiler、约束式 SQL generator、verified-query/hybrid。优先处理高频且高风险根因，同时复核：

- plan-SQL semantic validator；
- 全十视图 readiness；
- Decimal、单位和时区；
- 同一请求 snapshot/dataAsOf；
- EXPLAIN scan/cost gate；
- 稳定游标和大导出；
- 通用澄清/拒答与实体解析；
- partial success 的 API/UI 状态；
- schema/metric drift regression。

接手者可以根据基线重排这些项，不必把本文清单一次全部实现。

### 阶段 5：封存内部 holdout 和人工审批证据

开发集达到稳定后再建立未暴露 holdout，执行双人复核/仲裁或同等可靠流程。正式报告应同时展示点估计、分母、置信区间、关键切片、badcase 和适用边界。

### 阶段 6：公开 benchmark 与线上 shadow

- 公开集走隔离 adapter，只声称通用能力；
- 内部通过后先进行 shadow/只读试点，观察管理员修正、重问、放弃、导出和反馈；
- 在线接受率、点击或导出率是使用信号，不应自动当作答案准确率；
- 定期从真实问题抽样人工审核并进入新版本回归集。

阶段顺序可以按资源调整，但“先知道当前错在哪里，再宣布优化有效”应保留。

## 11. 接手后的建议首轮检查

所有 Python 命令使用 Conda `shop`：

```bash
cd /home/song/code/Java/AI_Shop
git status --short --branch
git branch -a -vv
git stash list

cd AI_Shop-backend/AI_Shop-agent
conda run --no-capture-output -n shop pytest -q \
  tests/test_data_analyst_service.py \
  tests/test_analytics_governance.py

conda run --no-capture-output -n shop pytest -q \
  tests/test_multi_agent_harness.py::test_data_analyst_clarifies_ambiguous_sales_ranking_without_model \
  tests/test_multi_agent_harness.py::test_sql_guard_accepts_catalog_query_and_rejects_escape_attempts

conda run --no-capture-output -n shop ruff check \
  app/services/data_analyst_service.py \
  app/services/sql_guard.py \
  app/services/analytics_catalog.py \
  app/services/analytics_export_service.py \
  app/db/analytics_pool.py \
  tests/test_data_analyst_service.py \
  tests/test_analytics_governance.py
```

在启动完整服务或连接真实数据库之前，先检查 `.env`、分析只读账号、provider 和 Java/MCP 依赖。不要为方便测试改用 root 或生产业务账号。若要运行端到端基线，优先使用隔离数据库和可回滚 fixture。

建议优先阅读：

- [项目 README](../../README.md)
- [AI_Shop 主线与开发记录](AI_Shop主线与开发记录.md)
- [评测体系全面审计与执行结果](../evaluation/AI-Shop评测体系全面审计与执行结果-20260826.md)
- [人工审批评测最终结果与 Badcase 分析](../evaluation/AI-Shop人工审批评测最终结果与Badcase分析-20260827.md)
- [评测资产与证据归档索引](../evaluation/评测资产与证据归档索引-20260826.md)

## 12. 留给接手者重新调研和决定的问题

这些问题故意不在本文中定死：

1. 目标用户究竟是运营、商品、供应链、客服主管还是财务？不同用户决定首批指标和容错成本。
2. 是否只支持十个视图，还是建设正式 semantic layer/metrics store？采用项目内 DSL 还是成熟外部方案？
3. SQL 应完全确定性编译、继续由 LLM 生成，还是 hybrid？应由同一评测集的覆盖率、正确率、延迟和维护成本决定。
4. 是否需要 join、窗口函数、同比/环比和 cohort？若需要，如何在扩大能力面时保持资源与语义治理？
5. 多轮澄清要做到何种深度，是否需要显式 conversation state 和 query revision？
6. 数据是查主库治理视图、只读副本、数仓还是定时快照？允许的数据新鲜度和一致性是什么？
7. 金额和财务类答案是否需要更严格审批、双算或“仅供运营参考”边界？
8. 导出是分析结果的延伸还是独立高容量产品？如何处理权限、脱敏、快照、过期和审计？
9. 选择哪些 provider/model，是否需要模型路由或本地模型？评测必须绑定具体版本和成本。
10. 内部 gold 由谁最终签字，holdout 由谁保管，什么频率允许重新打开？
11. 哪些公开 benchmark 与真实目标最接近，适配成本是否值得？
12. 生产试点的 SLO、并发、成本、数据规模和失败升级路径应是什么？

接手 AI 应重新联网检索官方资料、近期论文/benchmark、企业 semantic layer 实践和安全指南；如新证据与本文建议冲突，应记录证据并选择更合理方案。

## 13. 证据陈述红线

后续报告至少应继续遵守这些边界：

- “测试通过”不等于“Text2SQL 业务答案正确”。
- “SQL 可执行”不等于“SQL 语义正确”。
- “一个数据库状态结果相同”不等于“查询等价”。
- “Exact SQL 不同”也不自动等于错误。
- “公开 benchmark 分数”不等于“AI-Shop 业务质量”。
- “未观察到越权”不等于“绝对安全”。
- “本地延迟”不等于“生产 SLO”。
- “管理员接受/导出”不等于“答案正确”，只能作为在线代理信号。
- 不把 development、verified-query 示例或已反复查看的数据称为 unseen holdout。
- 不把客服、RAG、Search 的质量指标迁移给 Text2SQL。
- 不删除失败包、旧 badcase 或审计链来改变当前结论。

## 14. 外部调研起点

下列资料是前期调研得到的起点，不是唯一权威清单。接手时应重新确认其版本、许可、数据下载方式和最新结论：

- [Snowflake：Semantic View 建模最佳实践](https://docs.snowflake.com/en/user-guide/views-semantic/best-practices-modeling)
- [Snowflake：Verified Query Repository](https://docs.snowflake.com/en/user-guide/views-semantic/verified-query-repository)
- [Snowflake：Cortex Analyst Evaluations](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst-evaluations)
- [Snowflake：Evaluating Cortex Agents](https://www.snowflake.com/en/developers/guides/best-practices-for-evaluating-cortex-agents/)
- [AWS/Cisco：企业级 NL2SQL 的 domain narrowing 方法](https://aws.amazon.com/blogs/machine-learning/enterprise-grade-natural-language-to-sql-generation-using-llms-balancing-accuracy-latency-and-scale/)
- [Spider 2.0](https://spider2-sql.github.io/)
- [BIRD](https://bird-bench.github.io/)
- [LiveSQLBench](https://livesqlbench.ai/)
- [BIRD-Interact](https://proceedings.iclr.cc/paper_files/paper/2026/hash/496b549556509bbb9770bf9d335c5800-Abstract-Conference.html)
- [Test Suite Accuracy](https://arxiv.org/abs/2010.02840)
- [TrustSQL](https://arxiv.org/abs/2403.15879)
- [Dr.Spider](https://github.com/awslabs/diagnostic-robustness-text-to-sql)
- [SecureSQL](https://aclanthology.org/2024.findings-emnlp.346.pdf)
- [Falcon 中文企业 Text2SQL benchmark](https://github.com/eosphoros-ai/Falcon)
- [DuSQL](https://aclanthology.org/2020.emnlp-main.562/)
- [CHASE](https://aclanthology.org/2021.acl-long.180/)
- [OWASP：Excessive Agency](https://genai.owasp.org/llmrisk/llm062025-excessive-agency/)

技术调研时优先阅读官方文档、原始论文和数据集仓库；社区榜单和厂商案例可以用于发现方向，但要验证评测协议和适用边界。

## 15. 一个宽松的阶段性完成定义

Text2SQL 首个“生产级候选”不必一次解决所有长尾，但至少应让团队能够诚实回答：

- 当前支持哪些业务问题、不支持哪些问题，谁确认了指标定义；
- 在什么固定数据库状态、权限和版本上进行了评测；
- 问题理解、SQL 结果、最终答案、安全和资源各自表现如何；
- 关键失败有哪些，用户会看到怎样的澄清、拒答或降级；
- 指标是否来自未暴露 holdout，是否经过人工审核/仲裁；
- 公开 benchmark 与内部质量分别说明了什么；
- 下一次 schema、metric、model 或 prompt 变化如何自动回归。

如果这些问题仍无法由证据回答，就应继续称为治理实验或受限试点，而不是为了赶进度包装为生产质量。反之，只要核心场景、风险边界和证据链可靠，接手者不必等待所有可选功能都完成才开始小范围 shadow 或只读试点。

本文的最终意图是给接手者一张可信地图，而不是替接手者作完所有决策。先复核仓库事实、建立真实基线，再让评测结果决定架构和优先级。
