# AI-Shop Text2SQL V0 建设与评测 Handoff

> 快照日期：2026-08-28（Asia/Shanghai）
>
> 工作区：`/home/song/code/Java/AI_Shop`
>
> 当前分支：`dev`
>
> 当前 HEAD：`4d3dd0b3c61ebace8354b791e95c89584d563073`
>
> 重要：本轮 Text2SQL 代码和证据大多仍是未提交工作区内容；不要用 HEAD 代表当前实现。

## 1. 新 Codex 先读什么

按下面顺序读取：

1. 本文。
2. `AI_Shop-backend/evaluation-evidence/benchmarks/text2sql/final-v0-20260828-run-002/REPORT.md`。
3. `AI_Shop-backend/evaluation-evidence/benchmarks/text2sql/final-v0-20260828-run-002/manifest.json`。
4. `AI_Shop-backend/AI_Shop-agent/evaluation/text2sql/README.md`。
5. 需要历史背景时再读 `docs/project/AI-Shop-Text2SQL生产级建设与评测Handoff-20260827.md`。

2026-08-27 handoff 中“尚无 Text2SQL 专项 gold、端到端基线和人工答案评审”的结论已经被本轮工作取代。旧文件仍有架构背景价值，但不能用来描述当前状态。

## 2. 一页结论

本轮约定的 Text2SQL V0 基础建设和评测流程已经完成：

- 建立了版本化 `PROVISIONAL` analytics catalog；
- 实现了稳定的 `outcome/completion` 合同、Decimal 字符串、单轮澄清、冻结分页和同结果导出；
- 单请求使用同一只读一致快照执行最多三个顺序分支；
- Java、Agent、Vue 接口合同和结构化 403 已打通；
- 建立了独立的 `evaluation/text2sql` CLI、MySQL/Redis fixture、80 条人工 gold、前后各 `80×3` 基线、自动配对比较和 canonical 输出 A/B/C 人工评审；
- 最终证据链和 SHA-256 校验已完成，评测临时容器已停止。

但质量结论同样明确：

> 工程基础和证据链完成，不等于答案质量达到生产门槛。

修复后 canonical 输出只有 `29/80` 获真人接受，仍有 `51/80` 被拒绝。因此最终状态固定为：

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

本轮没有新增 Join、窗口函数、同比环比、正式财务口径、确定性 compiler 或 verified-query 架构，也没有修改风险视图 DDL。

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

### 6.1 人工 canonical 结果（主要质量结论）

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

## 8. 测试和运行状态

最终验证记录：`AI_Shop-backend/evaluation-evidence/benchmarks/text2sql/verification-v0-20260828-final-001/verification.json`。

已通过：

- Python Text2SQL 定向：`60 passed, 2 deselected`
- 真实 MySQL fixture：`2 passed, 28 deselected`
- Java 定向：`9 passed, 0 failed`
- Vue：`4 passed`，生产构建通过
- 相关 Python ruff：通过
- `git diff --check`：通过
- gold、pre、accepted post、paired、A/B 和最终人工证据 SHA-256：通过

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

HEAD 仍是旧提交 `4d3dd0b`，本轮大量实现和 21MB 左右的 Text2SQL 证据仍未提交。新 Codex 必须先执行：

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

用户目前只要求了质量评估和 handoff；尚未明确授权实施 V1 架构重写。新 Codex 应先汇报选择与差异，等用户确认后再进行大改。

### 阶段 A：低风险合同修复

1. ABSTAIN 使用确定性模板，强制填充 `reasonCode`、非空 `dataAsOf`、`catalogVersion`、能力边界和 no-SQL 说明。
2. CLARIFY 改为 catalog 驱动的歧义规则和固定 choice ID，禁止模型自由生成无法编译的选项。
3. 所有 outcome 增加 required-facts/forbidden-claims 发送前 validator。
4. 数值、期间、单位和边界说明从类型化结果确定性渲染，模型不能自由复述数值。

这一步风险最低，直接覆盖 10 个 ABSTAIN、10 个 CLARIFY 和 25 个缺失事实问题，但只能在复跑后声明实际提升。

### 阶段 B：推荐采用 hybrid semantic compiler

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
- 优先治理 recommendation quality、tool quality、inventory forecast/risk、agent quality。

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
- 修复后人工接受率是 29/80，不能发布；
- 当前最优先的是非 ANSWER 合同修复和 ANSWER 语义正确性，而不是增加新 SQL 能力；
- reader 权限和 EXPLAIN 1345 决策不能为便利而放宽；
- 当前 80 条是已见 development 集，不是 unseen；
- 所有旧 evidence 必须保持不可变；
- 工作区有大量未提交和可能不相关的用户变更，禁止 reset/clean；
- 下一阶段架构和提交策略需要先向用户确认。

如果接手者得出不同技术路线，应以新证据说明差异，但不得改写现有人工结果或删除失败证据来改善结论。
