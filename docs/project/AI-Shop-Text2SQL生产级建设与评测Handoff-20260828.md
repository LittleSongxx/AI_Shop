# AI-Shop Text2SQL V0 建设与评测 Handoff

> 快照日期：2026-08-29（Asia/Shanghai；包含 2026-08-28 原始 handoff）
>
> 工作区：`/home/song/code/Java/AI_Shop`
>
> 当前分支：`dev`
>
> Stage C 源码检查点：`2744f58`（供应链 + 推荐/工具质量确定性编译；披露误报修正）
>
> 上一检查点：`3a89ed8`（供应链确定性编译）
>
> 重要：第 0 节是接手后的最新状态；与后文 2026-08-28 快照冲突时，以第 0 节为准。

## 0. 2026-08-29 接手续更

### 0.1 用户已确认的路线

用户确认 `1A、2A、3A、4A；unseen 暂缓`，随后允许自由调用外部模型并继续推荐方案。落实为：

- 先完成低风险确定性响应合同，再进入 compiler；
- 采用 hybrid semantic compiler，LLM 只负责结构化 semantic plan；
- 第一批 compiler 只覆盖供应链 `analytics_inventory_forecast` 和 `analytics_inventory_risk`；
- 已在后续检查点把 `analytics_recommendation_quality_daily` 与 `analytics_tool_quality_daily` 纳入确定性 compiler；
- 源码、测试、文档按检查点提交，evaluation evidence 单独保存，不混入 Git；
- 当前 80 条继续只作已见 development regression；新 unseen 暂缓；
- 不扩大 reader 权限、不修改十视图 DDL、不启动生产部署。

已形成的 Git 检查点：

```text
eb25e7e feat(text2sql): establish governed analytics runtime
da17e39 test(text2sql): archive V0 evaluation harness
25d653f docs(text2sql): record V0 handoff and evidence boundaries
d8c6c82 feat(text2sql): enforce deterministic response contracts
3a89ed8 feat(text2sql): compile supply analytics deterministically
e2f127d feat(text2sql): compile quality analytics deterministically
2744f58 fix(text2sql): avoid false timeout disclosures
```

### 0.2 Stage A、Stage B 和 Stage C 已完成

Stage A 已把 DENY、ABSTAIN、CLARIFY 和 ANSWER 的关键响应字段改为确定性合同，并修复 SQL guard 对 sqlglot `AND` 节点的识别。接受的 Stage A 基线是：

```text
AI_Shop-backend/evaluation-evidence/benchmarks/text2sql/
post-contract-v0-20260828-run-001
```

第一批 Stage B 在 `3a89ed8` 完成：

- `DataAnalysisBranch` 增加类型化 `filters`、`order_by`、`top_k`；
- LLM 仍生成 plan，但 10 个供应链契约由高置信槽位归一化器补齐 view、metric、grain、filter、order、topK 和当前快照日期；
- 新增 `analytics_semantic_compiler.py`，仅编译 forecast/risk；其他八视图仍走原有受 guard 约束的 LLM SQL；
- 已覆盖但不受支持的供应链 plan 以 `SEMANTIC_PLAN_UNSUPPORTED`、no-SQL ABSTAIN 失败关闭，禁止回退自由 SQL；
- compiler 只接受 catalog 字段和类型化操作符，固定稳定 tie-break，拒绝 MySQL 反斜杠/控制字符文本 filter；
- 供应链 required facts 和口径说明由 plan 确定性渲染，不让模型自由补写关键边界；
- 显式“分别返回两张表、不做跨视图 Join”不再被 Join abstain 规则误伤；
- query trace 增加 `sqlSource=DETERMINISTIC_COMPILER`；运行版本更新为 `v2-supply-compiler`；
- Java 下载接口先复制只读 upstream headers，再设置 JSON content type 和 disposition，修复成功导出被 `UnsupportedOperationException` 包装成空 `data` 的问题；
- input freeze 已纳入 compiler 和 policy 源码，最终证据能绑定实际执行实现。

Stage C 在 `e2f127d`、`2744f58` 完成：

- 推荐质量视图按 VERIFIED 事件发生日编译明细、按商品聚合、稳定排序和聚合筛选；
- 工具质量视图按技术调用状态编译明细、失败 Top 1 和工具聚合；加权平均、NULL 和金额等不受支持语义仍不会被自由 SQL 猜测；
- 质量视图结果由确定性 renderer 补齐 event-day、VERIFIED、technical-call-status、period、dataAsOf、catalogVersion 及导出权限边界；
- t007/t048 当前均等待真实 branch-level fault injection；现阶段只依据观测到的分支状态披露 timeout，其他错误不会被误称为 timeout；
- 编译分支 trace 标记 `sqlSource=DETERMINISTIC_COMPILER`，运行版本为 `v3-quality-compiler`；
- 未扩大 reader 权限、未修改十视图 DDL，未启用 unseen 或生产部署。

### 0.3 Stage B 供应链 80×3 development regression

与最终源码对应、SHA-256 全包校验通过的主证据是：

```text
AI_Shop-backend/evaluation-evidence/benchmarks/text2sql/
post-compiler-v0-20260829-run-002
```

`post-compiler-v0-20260829-run-001` 是加入最后一条 MySQL 文本字面量失败关闭前的中间证据，保持不可变，但不作为最终源码主结论。

相对 Stage A `post-contract-v0-20260828-run-001`，canonical trial 变化如下：

| 指标 | Stage A | Stage B run-002 | 变化 |
|---|---:|---:|---:|
| outcome | 66/80 | 76/80 | +10 |
| trusted request | 24/80 | 35/80 | +11 |
| completion | 64/80 | 75/80 | +11 |
| plan | 25/48 | 35/48 | +10 |
| execution | 32/48 | 43/48 | +11 |
| denotation | 19/48 | 28/48 | +9 |
| narrative | 17/80 | 27/80 | +10 |
| flow | 15/32 | 23/32 | +8 |
| infrastructure failures | 8 | 2 | -6 |
| severe security failures | 0 | 0 | 持平 |

三轮合计 trusted request 从 `73/240` 提升到 `108/240`，flow 从 `42/96` 提升到 `71/96`，infrastructure failures 从 `27` 降到 `9`；这些是已见集回归数据，不是 unseen 或生产准确率。

本轮目标供应链切片结果是稳定的：

| 供应链 019–028，三轮合计 | Stage A | Stage B run-002 |
|---|---:|---:|
| trusted request | 0/30 | 30/30 |
| outcome | 13/30 | 30/30 |
| plan | 13/30 | 30/30 |
| execution | 8/30 | 30/30 |
| denotation | 8/30 | 30/30 |
| narrative | 0/30 | 30/30 |
| flow | 19/30 | 30/30 |
| infrastructure / severe security | 0 / 0 | 0 / 0 |

30 个请求实际产生 33 个分支（双表 case 每轮两个分支），全部记录为 `DETERMINISTIC_COMPILER`。两个供应链视图在 manifest 中也分别为 `15/15 trusted`。

全量 canonical 仍只有 `35/80 trusted`，且三轮全量 trusted 为 `108/240`。非 compiler 视图仍受模型计划、自由 SQL 和叙述波动影响；例如 run-002 相对 Stage A 的 canonical trusted 有 12 例改善、1 例回退，不能把整体增量全部归因于 compiler。最终边界保持：

```text
development=true
provisional=true
unseen=false
releaseGateEligible=false
```

### 0.3.1 Stage C 质量 compiler 80×3 development regression

与 `2744f58` 源码检查点对应的正式证据包为：

```text
AI_Shop-backend/evaluation-evidence/benchmarks/text2sql/
post-quality-compiler-v0-20260829-run-002
```

该目录由官方签字 gold
`gold-v0-20260828/adjudicated/gold-v0.jsonl` 生成，`input-freeze` 固定
`HEAD=2744f584e941491b6f6fb1285463d9a9ea0f5ecd`；目录内 `SHA256SUMS` 全部通过。
模型调用存在自然波动，因此它是新版本的独立回归包，不覆盖或替代前一包
`post-quality-compiler-v0-20260829-run-001`。

run-002 canonical（trial 1）和三轮合计指标如下：

| 指标 | canonical 80 条 | 三轮 240 条 |
| --- | ---: | ---: |
| outcome | 78/80 | 231/240 |
| completion | 77/80 | 228/240 |
| plan | 39/48 | 115/144 |
| execution | 45/48 | 132/144 |
| denotation | 36/48 | 106/144 |
| narrative | 34/80 | 102/240 |
| flow | 27/32 | 80/96 |
| policy | 78/80 | 231/240 |
| sqlPlanConsistency | 46/48 | 135/144 |
| trusted request | 44/80 | 131/240 |
| infrastructure failures | 1 | 6 |
| severe security failures | 0 | 0 |

canonical 延迟 P50/P95/P99 为 `4049.350/7131.833/11314.096 ms`；三轮完整决策稳定性
为 `64/80`，outcome 稳定性为 `78/80`。这些仍是已见 development regression，不能解释为生产准确率。
本包尚未重新进行 A/B/C 人工 answer-review；后文 `29/80 ACCEPT` 只属于旧的
`post-foundation` 人工评审，不能套用于本 Stage C run。

本阶段已实际编译并逐案验证的 7 个质量目标为 `t2s-v0-033`–`036`（推荐质量）和
`t2s-v0-045`–`047`（工具质量）：canonical `7/7 trusted`，三轮合计 `21/21 trusted`，
对应分支 trace 均为 `sqlSource=DETERMINISTIC_COMPILER`。两个质量视图的 canonical
trusted 分别为推荐 `4/4`、工具 `3/4`；工具视图的第 4 个 case 是降级 case，不应从分母删除。

质量切片之外的两个降级 case 需要单独看待：官方 gold 只有
`t2s-v0-007`（fulfillment 分支）和 `t2s-v0-048`（agent_quality 分支）声明
`flow.fault=BRANCH_2_TIMEOUT`。当前 `FlowContract.fault` 只是长度受限字符串，
`evaluation/text2sql/runner.py` 不读取/注入该字段，scorer 对没有 flow check 的 case 返回
`applicable=false, passed=true`，也没有 `FAULT_INJECTED` 或 production-boundary 事件。
run-002 三轮两例均观测到 branch 2 `SUCCEEDED`、整体 `COMPLETE`，而 gold 期望
`PARTIAL`；因此两例的 completion/execution/narrative 不通过，不能称为已完成 timeout
演练，也不能把自然数据库错误归因于 timeout。该问题是评测 harness capability gap，
不是业务数据或恢复能力的正面结论。

### 0.4 Stage C 验证与运行状态

最终源码验证：

- Python README 全定向（`-m 'not mysql'`）：`141 passed, 2 deselected`（另有 1 个 Starlette deprecation warning）；质量/compiler 两文件回归为 `122 passed, 1 skipped`；
- 相关 ruff：通过；
- Java `AgentMessageControllerDataAnalystTest`：通过；
- 真实 MySQL 8.4.11 十视图可读、源表/写操作/跨库读取拒绝：通过；
- `post-quality-compiler-v0-20260829-run-002` `SHA256SUMS`：全部通过；
- `git diff --check`：通过；
- 定向外部模型诊断验证供应链 `10/10 trusted`；正式 Stage C 验证上述 7 个质量目标三轮 `21/21 trusted`，供应链旧切片仍为 `30/30 trusted`；
- Text2SQL Agent/Admin 隔离进程和 MySQL/Redis fixture 已停止。

仓库级 Python 全量测试此前仍有 7 个失败，原因仅是未提供既有私有 Search/RAG holdout：

```text
AI_Shop-backend/AI_Shop-agent/evaluation/.holdouts/
final-holdout-20260822-ai-quality-v9.jsonl
```

unseen 已明确暂缓，不得伪造该文件或把相关测试宣称为通过。

### 0.5 当前工作区边界和下一步

`2744f58` 之后，仍应排除以下不属于本检查点的 dirty 内容：

- 根目录历史中文面试文档的 tracked deletion；
- `AI_Shop-backend/AI_Shop-agent/.privacy-exports/`；
- `AI_Shop-backend/evaluation-evidence/`（证据独立保存，不进入 Git）；
- 根目录 `adjudication.open.jsonl`、`answer-review.completed-a.jsonl`、`answer-review.completed-b.jsonl`。

不要 reset、clean、恢复或顺手提交上述内容。

下一步暂按低风险路线 A 保持生产链路不变：把两个 fault case 标为未闭环的 diagnostic/shadow，
继续修复 required-facts/renderer 和非 compiler 视图的语义失败。若要正式关闭降级缺口，必须先确认以下路线：

- **A（当前默认）**：不改生产 Java→Python 链路，只保留声明性 fault 的诚实失败和单元测试；风险最低，但 t007/t048 不能声称完成故障演练。
- **B**：新增独立 `data-analyst-branch` HMAC capability，经 Java→Python 透传并在真实 branch-2 执行边界注入，保留 SQL/lineage/`FAULT_INJECTED` 事件；证据闭环更强，但需跨 Java、Python、runner、scorer 和安全开关审查，并重跑新 evidence。
- **C**：新建经双盲/C 仲裁的 gold-v1 改写预期；不改运行时，但旧 HUMAN_REVIEWED gold-v0 不可覆盖，且会改变降级合同覆盖范围。

在用户确认 B 或 C 前，不实施对应路线。unseen 继续暂缓；任何新视图 compiler、正式门槛、
业务 DDL 或 reader 权限变化都应生成新证据目录，不得覆盖既有 run-001/run-002。

## 1. 新 Codex 先读什么

按下面顺序读取：

1. 本文。
2. `AI_Shop-backend/evaluation-evidence/benchmarks/text2sql/final-v0-20260828-run-002/REPORT.md`。
3. `AI_Shop-backend/evaluation-evidence/benchmarks/text2sql/final-v0-20260828-run-002/manifest.json`。
4. `AI_Shop-backend/AI_Shop-agent/evaluation/text2sql/README.md`。
5. 本阶段主证据 `AI_Shop-backend/evaluation-evidence/benchmarks/text2sql/post-quality-compiler-v0-20260829-run-002/manifest.json` 及 `scores.jsonl`。
6. 需要历史背景时再读 `docs/project/AI-Shop-Text2SQL生产级建设与评测Handoff-20260827.md`。

2026-08-27 handoff 中“尚无 Text2SQL 专项 gold、端到端基线和人工答案评审”的结论已经被本轮工作取代。旧文件仍有架构背景价值，但不能用来描述当前状态。

## 2. 一页结论

本轮约定的 Text2SQL V0 基础建设和评测流程已经完成：

- 建立了版本化 `PROVISIONAL` analytics catalog；
- 实现了稳定的 `outcome/completion` 合同、Decimal 字符串、单轮澄清、冻结分页和同结果导出；
- 单请求使用同一只读一致快照执行最多三个顺序分支；
- 已完成供应链、推荐质量和工具质量受支持子集的 hybrid semantic compiler，并保留未支持计划的失败关闭；
- Java、Agent、Vue 接口合同和结构化 403 已打通；
- 建立了独立的 `evaluation/text2sql` CLI、MySQL/Redis fixture、80 条人工 gold、前后各 `80×3` 基线、自动配对比较和 canonical 输出 A/B/C 人工评审；
- 最终证据链和 SHA-256 校验已完成，评测临时容器已停止。

但质量结论同样明确：

> 工程基础和证据链完成，不等于答案质量达到生产门槛。

上一 `post-foundation` 包的人工复审中，修复后 canonical 输出只有 `29/80` 获真人接受，仍有 `51/80` 被拒绝；
Stage C 主包尚未重新进行人工 answer-review，不能把该 `29/80` 套用于质量 compiler run。因此最终状态固定为：

```text
development=true
provisional=true
unseen=false
releaseGateEligible=false
```

禁止据此启动生产部署、公开 benchmark 或声称生产准确率、unseen 泛化、正式财务口径或发布就绪。

## 3. V0 范围和已经确认的决策

### 3.1 能力范围

V0 只覆盖现有十个治理视图和现有最小 RBAC：

- `analytics_sales_daily`
- `analytics_product_sales_daily`
- `analytics_fulfillment_after_sales_daily`
- `analytics_inventory_risk`
- `analytics_inventory_forecast`
- `analytics_agent_quality_daily`
- `analytics_tool_quality_daily`
- `analytics_recommendation_funnel_daily`
- `analytics_recommendation_quality_daily`
- `analytics_offer_quality_daily`

本阶段没有新增 Join、窗口函数、同比环比、正式财务口径或 verified-query 架构；确定性 compiler 目前只覆盖供应链、推荐质量和工具质量的受支持子集，也没有修改风险视图 DDL。

默认币种为 `CNY`，时区为 `Asia/Shanghai`。评测固定时钟为 `2026-08-27 Asia/Shanghai`，生产配置禁止启用固定评测时钟。

### 3.2 语义边界

- 金额只能解释为暂定运营口径，必须带期间、`dataAsOf`、目录/口径来源和“暂定口径/仅供运营核对”；不能称为结算或审计结论。
- 履约视图按订单创建日聚合当前状态，不能解释成发货/完成事件 cohort。
- 推荐事件日比率不能称为正式转化率。
- forecast `confidence` 只能解释为数据覆盖度。
- 目录外、口径不可证明或高风险越界问题应 `ABSTAIN`，策略/权限拒绝应 `DENY`。

### 3.3 EXPLAIN 决策

用户已明确选择“安全优先”的方案：

- reader 仍只拥有十个视图的 `SELECT + SHOW VIEW`；
- 每个查询分支必须尝试 EXPLAIN；
- MySQL 8.4 对视图 EXPLAIN 返回 1345 时，记录结构化诊断 `EXPLAIN_UNAVAILABLE_VIEW_PRIVILEGE`，`scanEstimate=null`，查询继续；
- 其他 EXPLAIN 错误仍失败关闭；
- 不为获得底层执行计划而扩大 reader 的源表权限；
- V0 的扫描估计只诊断，不设置未经基线校准的硬阈值。

不要把 1345 降级改回“给 reader 源表 SELECT”。

## 4. 已实现的主要代码

### 4.1 Python Agent

核心文件：

- `app/resources/analytics-catalog-v0.provisional.json`
- `app/services/analytics_catalog.py`
- `app/db/analytics_pool.py`
- `app/services/analytics_policy.py`
- `app/services/analytics_clarification_service.py`
- `app/services/analytics_result_service.py`
- `app/services/analytics_export_service.py`
- `app/services/data_analyst_service.py`
- `app/api/routes/agent.py`
- `app/config/settings.py`

当前合同包括：

- `outcome = ANSWER | CLARIFY | ABSTAIN | DENY | null`
- `completion = COMPLETE | PARTIAL | FAILED | NOT_APPLICABLE`
- 基础设施、模型或数据库失败使用 `outcome=null, completion=FAILED`
- 成功结果返回 `catalogVersion`、`dataAsOf`、`columnTypes`、`resultSetId` 和快照过期时间
- MySQL Decimal 在 JSON 中输出规范字符串，金额固定两位小数
- 首次结果最多冻结 200 行到 Redis 15 分钟
- v2 HMAC cursor 绑定结果集、owner/scope、offset 和过期时间；翻页不调用模型、不重跑 SQL
- Redis 快照失败时首次第一页仍返回，并带 `RESULT_SNAPSHOT_UNAVAILABLE`；后续依赖快照的操作明确返回 503，过期返回 410
- 导出必须提供 `resultSetId`，只导出同一冻结结果，异步 JSON 工件保留 24 小时；不再声称支持 10000 行
- 澄清 token 绑定 owner，TTL 15 分钟，只允许一轮；仍有关键歧义时转 `ABSTAIN`
- 一次请求只获取一个分析连接，使用 `REPEATABLE READ + START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY`
- 最多三个分支在同一事务中顺序执行，EXPLAIN 和查询共享快照及 `dataAsOf`

### 4.2 Java Admin/Common

主要改动：

- `AI_Shop-common/.../HttpBusinessException.java` 支持结构化 payload；
- `AI_Shop-common/.../AppInterceptor.java` 对策略或权限拒绝返回 HTTP 403，并在 `ResponseVO.data` 中携带 `outcome=DENY`、`reasonCode` 和关联 ID；
- `AgentMessageController`、`AgentMessageService`、`AgentMessageServiceImpl` 增加/透传 ask、clarify、page、export、export status/download；
- Agent 非 2xx 状态按原语义透传，不再全部抹平成普通成功响应。

管理端路径：

```text
/agentMessage/dataAnalyst/ask
/agentMessage/dataAnalyst/clarify
/agentMessage/dataAnalyst/page
/agentMessage/dataAnalyst/export
/agentMessage/dataAnalyst/export/status
/agentMessage/dataAnalyst/export/download
```

### 4.3 Vue Admin

主要文件：

- `AI_Shop-front/AI_Shop-admin/src/utils/dataAnalyst.js`
- `AI_Shop-front/AI_Shop-admin/src/utils/Api.js`
- `AI_Shop-front/AI_Shop-admin/src/utils/Request.js`
- `AI_Shop-front/AI_Shop-admin/src/views/data/DataAnalyst.vue`
- `AI_Shop-front/AI_Shop-admin/tests/data-analyst.test.js`

已支持 outcome 展示、PARTIAL 警示、Decimal 展示与图表数值副本、冻结结果下一页、快照过期提示，以及无 `resultSetId` 时禁用导出。

### 4.4 独立评测器

目录：`AI_Shop-backend/AI_Shop-agent/evaluation/text2sql/`

关键能力：

- catalog/dataset/lock 校验；
- MySQL 8.4.11 + Redis 临时 fixture；
- 按仓库真实 migration 创建十视图，不复制另一套视图 DDL；
- reader 的十视图最小权限验证；
- fixture base/boundary/empty 状态、oracle 和 mutation diagnostic；
- 隔离 Java→Agent 全链路 runner；
- gold OPEN、seal、compare、C adjudicate；
- baseline `80×3`、统一 scorer 配对比较；
- canonical 答案 A/B 双盲和仅 decision 分歧的 C 仲裁；
- 最终报告的证据绑定和失败关闭校验。

新增或重点文件包括：

- `answer_review.py`
- `comparison.py`
- `final_report.py`
- `runner.py`
- `scoring.py`
- `fixture.py`
- `runtime.py`
- `cli.py`

## 5. 不可变评测证据

根目录：

```text
AI_Shop-backend/evaluation-evidence/benchmarks/text2sql/
```

主要证据：

| 目录 | 含义 | 状态 |
| --- | --- | --- |
| `gold-v0-20260828/adjudicated/` | 80 条人工审核 gold | 完成，A/B/C，人类最终决策 |
| `pre-foundation-v0-20260828-run-001/` | 修复前 `80×3` | 不可变 |
| `post-foundation-v0-20260828-run-001/` | 第一版修复后包 | 不可变但不采纳；opaque ID 被手机号正则误报 |
| `post-foundation-v0-20260828-run-002/` | 接受的修复后 `80×3` | 不可变，严重安全失败 0 |
| `post-quality-compiler-v0-20260829-run-001/` | Stage C 质量 compiler 初始 `80×3`（`e2f127d`） | 不可变；后续 disclosure 修正后不作为主包 |
| `post-quality-compiler-v0-20260829-run-002/` | Stage C 质量 compiler + disclosure 修正 `80×3`（`2744f58`） | 不可变，SHA 全通过；当前主证据 |
| `paired-pre-post-v0-20260828-run-002/` | 同 scorer 的 canonical 前后配对 | 完成 |
| `answer-review-v0-20260828-a-sealed-001/` | canonical reviewer A | 完成，reviewer `song` |
| `answer-review-v0-20260828-b-sealed-001/` | canonical reviewer B | 完成，reviewer `yang` |
| `answer-review-v0-20260828-adjudicated-001/` | 160 项最终人工判断 | 完成，C 为 `lin` |
| `verification-v0-20260828-final-001/` | 测试和 SHA 验证记录 | 完成 |
| `final-v0-20260828-run-002/` | 最终 V0 报告和机器 manifest | 完成 |

关键哈希：

```text
gold-v0.jsonl:
b6bbc5b11f23d7ca6589735e77a628ee5eb99a984d05f72af79034c1a156c200

post run-002 SHA256SUMS:
11d59da5f8b5b23fcc04ec55c8c29e03db91b5fbeae5a35faa6f3abe4d30bcf4

final SHA256SUMS:
dfdf75b50dffce946f5126fa3dfff6b27b80015f6a8374bd09515a84ee6b9a25

final REPORT.md:
9166e60ac7cdc8f381d0cc57d015c9943348eadcc25d286c6ecc1eb56846d2e4

final manifest.json:
f578d2d0c1021711ae0c2700362dffc6db0be7347aeee497d461915aa2b7feb9

post-quality-compiler-v0-20260829-run-002 SHA256SUMS:
1abec4aa9d2fa6cd0be4d40b6cb37e2b76becaee484588cf002ab33e4c485270

post-quality-compiler-v0-20260829-run-002 manifest.json:
18e2fb82abb59361dfd95ddf306c914594cc715539bfcc62a4e68512cf126393

post-quality-compiler-v0-20260829-run-002 scores.jsonl:
700b35cc46df4caae6630620c815b6cff149741878f71af4240da13fe240c998

post-quality-compiler-v0-20260829-run-002 raw-responses.jsonl:
e06a45ae83bb0902bad4e961fb96b7436ae451b60570b0f82c7c594b47c4640e
```

任何复跑必须创建新的目录，例如 run-003；禁止覆盖旧包、修改旧响应或删除失败包。

项目根目录还保留了真人交回的原始文件：

```text
answer-review.completed-a.jsonl
answer-review.completed-b.jsonl
adjudication.open.jsonl
```

这些文件已经被封存到最终证据，但原始交回件仍应保留，不要改写。

## 6. 当前测评结果

### 6.1 Stage B 人工 canonical 结果（主要质量结论）

本节数字来自 `post-foundation` 的 A/B/C 人工评审，不是 Stage C
`post-quality-compiler-v0-20260829-run-002` 的人工接受率；Stage C 目前只有自动 scorer 证据。

A/B 共审核 160 个混合随机化 canonical 输出：

- A/B decision 一致 155 项；
- 仅 5 项交 C 仲裁；
- C 的 5 项最终均为 `ACCEPT`；
- 修复前：`0/80 ACCEPT`、`80/80 REJECT`；
- 修复后：`29/80 ACCEPT`、`51/80 REJECT`；
- 配对变化：29 项改善，51 项仍拒绝，没有人工判断回退。

修复后按 gold outcome：

| Outcome | ACCEPT | REJECT | 评价 |
| --- | ---: | ---: | --- |
| ANSWER | 18/48 | 30/48 | 结果和 SQL 语义是主要瓶颈 |
| CLARIFY | 0/10 | 10/10 | 澄清问题/选项合同不准确 |
| ABSTAIN | 0/10 | 10/10 | outcome 基本正确，但缺必要边界事实 |
| DENY | 11/12 | 1/12 | 当前表现最好 |

人工拒绝原因可重叠：

```text
WRONG_RESULT              31
MISSING_REQUIRED_FACT     25
WRONG_COMPLETION          19
WRONG_OUTCOME             17
CLARIFICATION_DEFECT      10
INFRASTRUCTURE_FAILURE     8
FLOW_CONTRACT_DEFECT       4
MISLEADING_BOUNDARY        3
```

### 6.2 自动 canonical 前后比较（诊断，不替代人工）

| 指标 | 修复前 | 修复后 |
| --- | ---: | ---: |
| Outcome | 44/80（55.0%） | 63/80（78.8%） |
| Completion | 57/80（71.2%） | 61/80（76.2%） |
| Plan | 29/48（60.4%） | 28/48（58.3%） |
| Execution | 33/48（68.8%） | 32/48（66.7%） |
| Denotation | 12/48（25.0%） | 23/48（47.9%） |
| Narrative | 0/80 | 20/80（25.0%） |
| Flow | 4/32（12.5%） | 7/32（21.9%） |
| Trusted request | 0/80 | 18/80（22.5%） |
| Ordinary trusted answer | 0/46 | 1/46（2.2%） |
| 严重安全失败 | 4 | 0 |

修复后全部 240 次运行：

- outcome `190/240`；
- completion `184/240`；
- denotation `72/144`；
- 严重安全失败 `0`；
- 基础设施/模型失败 `28`，全部保留，没有按 skip 处理；
- outcome 三次运行稳定性 `75/80`；
- 完整决策稳定性 `47/80`；
- canonical 延迟 P50 约 `4.95s`，P95 约 `10.49s`。

### 6.3 质量判断

- **安全与治理骨架：** 在当前已见集上表现较好，修复后 240 次严重安全失败为 0，DENY 前后源数据指纹未变化；不能外推成绝对安全。
- **答案正确性：** 不合格。人工总体接受率仅 36.3%，ANSWER 仅 37.5%。
- **澄清与拒答 UX：** 不合格。CLARIFY、ABSTAIN 的 outcome 自动指标看似较好，但真人因选项合同和必要事实缺失全部拒绝。
- **稳定性：** 不足。完整决策三次稳定性仅 58.8%。
- **性能：** 可运行但偏慢，P95 超过 10 秒。
- **总体：** 适合继续做内部开发回归，不适合生产或公开质量声明。

## 7. 已知失败根因

### 7.1 非 ANSWER 的低风险修复机会

ABSTAIN 样本通常已经选择正确 outcome 和 reasonCode，但缺少 `dataAsOf`、完整 catalog/capability boundary 或其他 required facts。应使用确定性 renderer 和发送前 required-facts validator，而不是继续调 prompt。

CLARIFY 的主要问题包括：

- 应澄清时直接回答；
- 选项缺少 gold 要求的时间、指标或第三个选项；
- 提供目录中不存在或口径不合适的候选指标；
- option 文案语义接近但不能稳定映射到合法 semantic plan。

### 7.2 ANSWER 的核心问题

48 个 ANSWER 只有 18 个获人工接受。拒绝最多的视图是：

```text
analytics_inventory_forecast                 4/5 rejected
analytics_inventory_risk                     4/5 rejected
analytics_recommendation_quality_daily       4/4 rejected
analytics_tool_quality_daily                 4/4 rejected
analytics_agent_quality_daily                3/4 rejected
analytics_product_sales_daily                3/6 rejected
analytics_recommendation_funnel_daily        3/4 rejected
```

主要问题是错误结果、错误 outcome/completion、缺必要事实和 SQL/模型失败。现有基础修复没有重写自由 SQL 生成，因此 plan 和 execution 指标没有提升，属于已知结果，不应继续用接口合同优化代替 SQL 语义优化。

### 7.3 28 个基础设施/模型失败

```text
SQL_FUNCTION_NOT_ALLOWLISTED     18
DATA_ANALYST_COLUMN_INVALID       6
DATA_ANALYST_PLAN_PARSE_FAILED    2
SQL_OR_FORBIDDEN                  1
DATABASE_UNAVAILABLE              1
```

不要简单扩大 allowlist。优先使用 schema-constrained semantic plan、字段枚举、确定性表达式编译和一次有界修复。

### 7.4 post run-001 的安全误报

`post-foundation-v0-20260828-run-001` 把随机 `resultHash/resultSetId` 中偶然出现的 11 位数字当作手机号，报告了 5 个严重失败。旧包未修改。scorer 后来只对明确的 opaque server ID/hash 字段排除手机号正则，同时保留答案、行和 SQL 的真实 PII 扫描，并加入“随机 hash 不误报、真实手机号仍命中”的测试。接受的修复后包是 run-002。

## 8. 历史验证与当前运行状态

最终验证记录：`AI_Shop-backend/evaluation-evidence/benchmarks/text2sql/verification-v0-20260828-final-001/verification.json`。

以下是原 V0 foundation 验证记录（保留作历史基线）：

- Python Text2SQL 定向：`60 passed, 2 deselected`
- 真实 MySQL fixture：`2 passed, 28 deselected`
- Java 定向：`9 passed, 0 failed`
- Vue：`4 passed`，生产构建通过
- 相关 Python ruff：通过
- `git diff --check`：通过
- gold、pre、accepted post、paired、A/B 和最终人工证据 SHA-256：通过

本次 Stage C 还完成了 README 全定向非 MySQL 回归 `141 passed, 2 deselected`，质量/compiler
两文件回归 `122 passed, 1 skipped`；`post-quality-compiler-v0-20260829-run-002` 的
`SHA256SUMS` 全部通过，且 t033–036/t045–047 三轮 `21/21 trusted`。该包尚未人工复审。

仓库级 Python 全量结果为：

```text
1665 passed, 9 skipped, 7 failed
```

7 个失败全部由缺少既有私有 Search/RAG holdout 引起：

```text
AI_Shop-backend/AI_Shop-agent/evaluation/.holdouts/
final-holdout-20260822-ai-quality-v9.jsonl
```

受影响的是 `tests/test_quality_scorecard.py` 和 `tests/test_search_paired_replay.py`。不要伪造、重建或把它们标成通过；它们不属于 Text2SQL 范围，但全量测试仍应如实报告失败。

评测专用 MySQL/Redis fixture 已执行 `fixture-down`，当前没有 Text2SQL 临时容器。仓库原有 MySQL/Redis 没有被停止或修改。

## 9. 当前 Git/工作区注意事项

### 9.1 当前不是干净分支

当前源码 HEAD 为 `2744f58`；evaluation evidence 仍按约定独立保存、不进入 Git。新 Codex 必须先执行：

```bash
cd /home/song/code/Java/AI_Shop
git status --short --branch
git diff --check
git stash list
```

不要执行：

- `git reset --hard`
- `git checkout -- <path>`
- `git clean`
- 随意 apply/drop stash
- 覆盖或删除 evaluation evidence

当前仍有两个历史 stash：

```text
stash@{0}: pre-branch-consolidation-untracked-20260827
stash@{1}: old ai-app-agent-quality worktree
```

它们不是本轮 Text2SQL 实现，不要混入。

### 9.2 不要误处理其他用户改动

工作区包含文档修改、一个历史中文文档删除项、`.privacy-exports/` 等内容。并非所有 dirty 项都能确认属于 Text2SQL。提交或整理前必须逐项审查；不要为了让状态干净而删除、恢复或一起提交。

`AI_Shop-backend/evaluation-evidence/` 当前未跟踪但属于本轮关键证据。若用户要求提交，应先确认仓库对大证据包、隐私和 Git 体积的策略，不要自行决定全部纳入版本控制。

## 10. 推荐的后续优化路线

用户目前已接受并完成供应链、推荐质量和工具质量的 compiler 切片；尚未明确授权实施 V1 架构重写或跨链路 fault injection。新 Codex 应先汇报选择与差异，等用户确认后再进行大改。

### 阶段 A：低风险合同修复

1. ABSTAIN 使用确定性模板，强制填充 `reasonCode`、非空 `dataAsOf`、`catalogVersion`、能力边界和 no-SQL 说明。
2. CLARIFY 改为 catalog 驱动的歧义规则和固定 choice ID，禁止模型自由生成无法编译的选项。
3. 所有 outcome 增加 required-facts/forbidden-claims 发送前 validator。
4. 数值、期间、单位和边界说明从类型化结果确定性渲染，模型不能自由复述数值。

这一步风险最低，直接覆盖 10 个 ABSTAIN、10 个 CLARIFY 和 25 个缺失事实问题，但只能在复跑后声明实际提升。

### 阶段 B：hybrid semantic compiler（当前已完成部分视图）

推荐架构：

```text
问题
  -> 确定性 DENY / ABSTAIN / CLARIFY 路由
  -> LLM 只输出 semantic plan
     {view, metrics, dimensions, dates, filters, order, topK}
  -> catalog/schema/answerability validator
  -> 十视图的确定性 SQL compiler
  -> SQL AST 安全校验
  -> 同一只读快照执行
  -> 类型化结果校验
  -> 确定性答案 renderer
```

建议：

- 当前十视图、单视图范围优先由 compiler 覆盖；
- 高频或财务敏感问题使用 verified query；
- 暂不支持的 plan 明确 ABSTAIN，不回退到无约束自由 SQL；
- 固化 ratio 的分子/分母、加权平均、NULL、Decimal、日期归属、Top/Bottom、tie 和排序规则；
- 已完成 recommendation quality、tool quality、inventory forecast/risk 的受支持子集；下一批若扩展 agent quality 或复杂 ratio/加权平均，必须先补 catalog 语义和新 evidence。

不建议把下一阶段主要投入继续放在 prompt tuning。当前 31 个 WRONG_RESULT、18 个 function allowlist 失败和 6 个 invalid column 说明仅调 prompt 难以得到稳定语义保证。

### 阶段 C：可靠性、流程和性能

- 使用 JSON schema/tool calling 或等价 schema-constrained plan 消除 parse failure；
- 对合法但表达式不受支持的计划做确定性转换或一次有界修复；
- 继续保持失败关闭，不为“通过率”放宽源表、PII、写操作或跨库边界；
- 在上游正确后重新测 pagination/export/clarification flow；
- 缩小 prompt、缓存 catalog、减少模型调用，降低 P50/P95；
- 保持单事务快照和冻结结果分页，不回到重新执行 SQL 的 cursor。

### 阶段 D：新 unseen 评测与发布门槛

- 当前 80 条已经反复查看，只能作为 development regression，不能改名为 unseen；
- 新建独立 unseen 集，继续双真人盲审及必要 C 仲裁；
- 在新数据上同时报告人工答案、denotation、安全、稳定性、延迟和关键切片；
- 发布阈值需用户/业务负责人确认。可讨论的候选门槛是：严重安全失败 0、基础设施失败率不高于 1%、人工接受率至少 90%、完整决策稳定性至少 95%；这些不是当前已批准门槛。

在核心正确性稳定前，不建议增加 Join、窗口函数、同比环比或扩大正式财务能力面。

## 11. 新 Codex 必须向用户确认的开放决策

开始 V1 大改前，应把以下选项和取舍说清楚并等待用户选择：

1. 是否按推荐的 hybrid semantic compiler 推进，还是继续约束式 LLM SQL，或采用 verified-query 优先。
2. 第一批要达标的用户角色和视图：运营、供应链、推荐/Agent 质量，还是财务敏感场景。
3. 是否先只修 ABSTAIN/CLARIFY/renderer 的低风险问题，再开始 compiler。
4. unseen 集规模、保管人、A/B/C 人员和允许打开的时间点。
5. V1 的人工质量、稳定性、失败率和延迟门槛。
6. 当前未提交工作区应该怎样拆分提交，以及 21MB evidence 是否进入 Git。

不要擅自启动生产部署、创建公开 benchmark、修改十视图业务 DDL、扩大 reader 权限或清理用户工作区。

## 12. 常用复核命令

### 12.1 证据

```bash
cd /home/song/code/Java/AI_Shop/AI_Shop-backend/evaluation-evidence/benchmarks/text2sql

(cd gold-v0-20260828/adjudicated && sha256sum -c SHA256SUMS)
(cd pre-foundation-v0-20260828-run-001 && sha256sum -c SHA256SUMS)
(cd post-foundation-v0-20260828-run-002 && sha256sum -c SHA256SUMS)
(cd paired-pre-post-v0-20260828-run-002 && sha256sum -c SHA256SUMS)
(cd answer-review-v0-20260828-adjudicated-001 && sha256sum -c SHA256SUMS)
(cd final-v0-20260828-run-002 && sha256sum -c SHA256SUMS)
(cd post-quality-compiler-v0-20260829-run-001 && sha256sum -c SHA256SUMS)
(cd post-quality-compiler-v0-20260829-run-002 && sha256sum -c SHA256SUMS)
```

### 12.2 Python 定向测试

```bash
cd /home/song/code/Java/AI_Shop/AI_Shop-backend/AI_Shop-agent

uv run pytest -q \
  tests/test_data_analyst_service.py \
  tests/test_analytics_governance.py \
  tests/test_analytics_clarification.py \
  tests/test_analytics_pool.py \
  tests/test_data_analyst_api.py \
  tests/test_text2sql_evaluation.py \
  -m 'not mysql'
```

真实 MySQL 测试必须先启动隔离 fixture，不能连接生产库：

```bash
uv run python -m evaluation.text2sql.cli fixture-bootstrap --state base
TEXT2SQL_EVAL_MYSQL_TESTS=1 uv run pytest -q \
  tests/test_analytics_pool.py tests/test_text2sql_evaluation.py -m mysql
uv run python -m evaluation.text2sql.cli fixture-down
```

### 12.3 Java 与 Vue

```bash
cd /home/song/code/Java/AI_Shop/AI_Shop-backend
mvn -q -pl AI_Shop-admin -am \
  -Dtest=AdminRbacMatrixTest,AgentMessageControllerDataAnalystTest,AgentMessageServiceImplDataAnalystTest \
  -Dsurefire.failIfNoSpecifiedTests=false test

cd /home/song/code/Java/AI_Shop/AI_Shop-front/AI_Shop-admin
npm test -- --run tests/data-analyst.test.js
npm run build:app -- --logLevel error
```

### 12.4 评测 CLI

```bash
cd /home/song/code/Java/AI_Shop/AI_Shop-backend/AI_Shop-agent
uv run python -m evaluation.text2sql.cli --help
```

完整命令和证据生命周期见 `evaluation/text2sql/README.md`。复跑必须使用新输出目录，旧 evidence 永不覆盖。

## 13. 接手完成标准

新 Codex 读完本文后，应能准确复述：

- V0 工程和评测流程已经完成，不需要从零重建；
- 上一 post-foundation 包人工接受率是 29/80，不能发布；Stage C 质量 compiler 主包尚未人工复审；
- 当前最优先的是非 ANSWER 合同、required-facts/renderer 和 fault-harness 边界，而不是无证据增加新 SQL 能力；
- 质量 compiler 已覆盖推荐质量/工具质量/供应链的受支持子集，t007/t048 的 branch-level fault 仍未闭环；
- reader 权限和 EXPLAIN 1345 决策不能为便利而放宽；
- 当前 80 条是已见 development 集，不是 unseen；
- 所有旧 evidence 必须保持不可变；
- 工作区有大量未提交和可能不相关的用户变更，禁止 reset/clean；
- 下一阶段架构和提交策略需要先向用户确认。

如果接手者得出不同技术路线，应以新证据说明差异，但不得改写现有人工结果或删除失败证据来改善结论。
