# AI_Shop 主线与开发记录

> 当前发布：`release-20260822-ai-quality-v9` / `final-20260822-ai-quality-v9`
> 最近更新：2026-08-29（Asia/Shanghai）
> Python 评测环境：Conda `shop`

> **当前排期冻结标记：** Text2SQL 为 `FROZEN` / `EXPERIMENTAL`，只保留兼容代码与历史证据；不新增功能、不跑 unseen、不进入默认演示。详见 [Text2SQL 冻结说明](AI-Shop-Text2SQL冻结说明-20260829.md)。

## 结论

项目可清晰描述为两条互相支撑的闭环：

```text
商品需求 -> BM25/向量召回 -> RRF/rerank -> Java 商品/库存/报价快照
        -> 预算/品牌/排除硬约束 -> 候选内解释 -> 浏览/加购/支付归因

客服问题 -> 意图/槽位 -> RAG 或 Java 权威工具 -> 只读回答或写提案
        -> 用户确认 -> Java 身份/状态/幂等校验 -> 成功、INCONCLUSIVE 或 MANUAL_REVIEW
```

模型不直接生成可执行商品事实，也不直接写订单、库存或支付；最终状态以 Java 权威数据和持久化 Episode 为准。当前闭环工程完整，
但不等于已经证明 CTR、CVR、GMV、生产容量或端到端客服答案准确率。

## 2026-08-29：主线收敛与后续排期

- 默认求职叙事只保留 AI 购物导购和 AI 客服 Agent 两条产品闭环；视觉搜索是导购子线。
- Text2SQL 降级为内部治理实验，状态为 `FROZEN`；其 DataAnalyst/InventoryOps 共享代码继续保留，避免兼容性回归。
- 后续工程按“真实流式/重连 → 取消终态/幂等 → Action 快照/对账 → checkpoint/记忆 → 模型工具成本 → 身份安全 → RAG 生命周期 → 可观测背压 → 前端真链路/a11y/perf”推进。
- 现有离线和人工证据只支持受控预生产演示，不推导线上 SLO、CTR/CVR/GMV、CSAT/FCR 或无人值守高并发能力。
- E1/E2/E3/E7/E4/E5/E6/E8/E9 已分别收口；E5 新增服务端 principal、WS Origin/帧限制/Redis 限流、Cookie CSRF 和 session TTL 防护；E6 收口发布目录 ACL、freshness、逻辑删除/回滚和 DLQ 可观测；E8 补齐 WebSocket 慢连接 bounded fan-out、listener liveness、队列年龄/取消时延和跨链路 correlation 指标；E9 收口 typed legacy card adapter、浏览器 token refresh、a11y/reduced-motion、Web Vitals 采集和 mock 浏览器闭环。验证仍是受控预生产证据，详见 [E5 身份与安全验证](AI-Shop-E5-身份与安全验证-20260829.md)、[E6 RAG 生命周期验证](AI-Shop-E6-RAG生命周期验证-20260829.md)、[E8 可观测背压短稳态验证](AI-Shop-E8-可观测背压短稳态验证-20260829.md) 与 [E9 前端真实闭环验证](AI-Shop-E9-前端真实闭环验证-20260829.md)。
- E1/E2/E3 的批次证据分别见 [流式重连](AI-Shop-E1-流式重连验证-20260829.md)、[取消终态幂等](AI-Shop-E2-取消终态幂等验证-20260829.md) 和 [检查点记忆恢复](AI-Shop-E3-检查点记忆恢复验证-20260829.md)；三批均只覆盖受控回归，不升级为生产 SLO 或容灾结论。
- 运行态清理已在服务停机后完成：日志、PID/observability、runtime 配置、Text2SQL runtime 日志和 22 个 privacy export 外置到可逆 quarantine；`run/data`、历史 observation、review workspace 和证据包保持原位，详见 [运行态清理索引](AI-Shop-运行态清理索引-20260829.md)。

## 按开发阶段的增量记录

### 2026-08-20：评测和可靠性骨架

- 将 Search、RAG、Agent 收敛到 `aishop-evaluation/v3`，统一 preflight、脱敏、哈希、数据锁、fail-closed 和 evidence lifecycle。
- 增加 Search 分片、metamorphic checks、RAG lexical/semantic shadow 证据、Agent `pass^k`/state diff、故障恢复矩阵、usage unknown 和 DB batch/N+1 benchmark。
- 明确质量指标与必须 100% 的安全/终态契约分开；本地延迟仅作诊断，不冒充生产 SLO。

### 2026-08-21：质量证据闭环

- 生成并一次执行 v9 final：Search 50、RAG 50、Agent 25；旧 final 保留为 immutable archive，`.runs` 只留主线运行。
- Search 主质量改为独立重算的 Recall@10、MRR@10、NDCG@10，并为每个指标保存 badcase；`50/50`、`25/25`、`pass^8` 只作为门禁/可靠性证据。
- 调查确认客服 Agent 原有 evidence 只有工具、终态和幂等，没有独立 intent/slot/handoff 金标，因此不能声称客服理解准确率。

### 2026-08-22：文档主线和客服理解证据

- 将日期版项目/质量说明合并到本文件和 [质量评测与 Badcase](../evaluation/AI质量评测与Badcase.md)，JSON scorecard 与客服 JSON 统一放入 `docs/evaluation/`。
- 新增独立客服 `gold-v1`（先 32 条，后扩展为 60 条），运行生产 `resolve_intent(..., allow_llm=False)` 规则预路由基线，输出四项核心指标和逐指标 badcase；扩展样本覆盖咨询/搜索边界、资金风险、隐私、否定、少件和货币槽位。
- 修复并回归验证支付扣款否定、重复/未授权支付、退款未到账、隐私数据与账号安全、否定转人工、破损短句和商品名 span 边界；当时 60 条 draft 的 provisional 诊断点估计曾为 Intent Macro-F1/高风险 Recall/slot span F1/slot EM/handoff Recall `1.000`、0 badcase。该结果未经过仲裁，现仅作为优化过程记录，不作为当前准确率主张。
- 额外修复首轮“支付方式有哪些”误建议转人工：`ASK_CLARIFICATION` 现在按低风险信息问法直接回答；第二次低置信度提示、连续第三次升级的既有契约保持不变。客服相关规则/路由回归 `155 passed`，全量 Python `1257 passed, 7 skipped`（7 项均为真实 MySQL 8 migration 条件跳过）。
- 将客服人工金标流程落成可执行 CLI：双人盲标导出、开放 sheet 封存、源数据/内容 SHA-256、taxonomy/切片一致性、递归脱敏校验、冲突仲裁和独立 HUMAN_VERIFIED 数据输出；新增专项测试 `18 passed`。当时仅完成流程工具，draft 结果仍不进 release gate。
- 暂不修复 Search hard negative、不重刷 Search 质量数据；先保留真实漏召回作为后续 paired replay 素材。

### 2026-08-23：客服人工金标与槽位口径冻结

- 两位标注者完成 60 条盲标，lead reviewer 对 25 条冲突逐 case 给出理由；案件完全一致 `35/60`，intent 一致率 `57/60`、Cohen κ `0.946`，risk 一致率 `56/60`、κ `0.890`，handoff `60/60`。
- 正式合并为独立 `HUMAN_VERIFIED` 数据集，证据包为 `customer-service-human-v1-20260823`；旧 pending 包和 provisional 报告只读保留，主 manifest 切换到新包但 `releaseGateEligible=false`。
- 规则预路由质量：Intent Macro-F1 `0.955299`（badcase `011/044/057`）、高风险 Recall `1.0`、handoff Recall `1.0`、critical miss `0/6`；完整人工 schema slot Span F1 `0.907652`、EM `0.558824`。
- 新增 production canonical slot 对齐诊断：Span F1 `0.992785`、EM `0.882353`；扩展槽位未映射与金额归一化单独分类，避免把 schema 差异误报为模型准确率下降。
- 发现并修复两项证据链问题：归档 sealed 文件搬迁后仍可由内容哈希校验；评测 CLI 支持仓库外临时 holdout 的 provenance。后续优先回归 `011/044/057/055/058`，再决定扩展槽位生产化。

### 2026-08-23：重启后全量复核与主线清理

- 基础设施复核：Conda `shop` 可用；Agent 全量 `1261 passed, 7 skipped`（仅真实 MySQL 8 migration 条件跳过），脚本契约 `58 passed`；Agent 目录与本轮变更脚本 Ruff、`compileall`、manifest、文档一致性和 `git diff --check` 均通过。仓库其余 `scripts/` 仍有 16 个既有 Ruff 风格/时区告警，未伪称全仓库 lint 清零。
- Java 后端 Maven reactor（26 个模块）`mvn -B -ntp test`：`BUILD SUCCESS`，无失败；1 个 RabbitMQ 外部依赖集成测试按条件跳过，未计入通过数。
- 重新核对 v9 当前结果：Search `Recall@10 macro=0.962121`、`micro=52/56=0.928571`、`MRR=0.937500`、`NDCG=0.920521`；RAG answerable Recall@5 `29/29`、lexical grounded/citation/no-answer `50/50`；Agent 25 case/200 trial，`pass^8=1.0`、重复副作用 `0`。这些分别是检索质量、安全证据和可靠性契约，不合并成一个“总分”。
- 质量报告补齐 Search/RAG/Agent 的 P50/P95/P99、usage/token 未知态、semantic shadow 可用性和指标级 badcase；客服 gold 明确记录双人一致率、仲裁数、稀疏 intent bootstrap 限制。
- 独立故障矩阵完成 `12` 个场景：生产边界 HARD `11/11`、harness boundary SHADOW `1/1`，全部 recovery contract 通过；正常 final 的 `resilienceMetrics=NOT_RUN` 保持不变，避免把辅助故障 run 混入普通质量分母。
- 独立 DB benchmark 在真实隔离数据库完成 `1/10/50/100` 候选规模的 batch/N+1 对照；100 候选 batch offer/decision P50 `23.864/2.405 ms`，N+1 P50 `89.805/70.501 ms`，均为本地描述性证据。
- 精确删除 19 个已被主线替代的旧 DB/repeat/fault benchmark 包，以及仓库根目录与 sealed 证据重复的 `adjudication.final.jsonl`；v2 通过 final、v3-v8 失败 final 和客服历史 pending/provisional 包仍作为只读追溯证据保留，`.runs` 只剩 v9 development/regression/final。

### 2026-08-23：客服全链路、样本扩展与 Search 成对回放

- 新增客服 HTTP 评测器，60 条 HUMAN_VERIFIED gold 全部走正式 Agent/Java/RAG/LLM 路径；执行 `60/60`，HTTP Intent Macro-F1 `0.955299`，handoff `TP=14/TN=46/FP=0/FN=0`。
- 修正两个证据口径：HTTP Episode 脱敏槽位不再计分；Slot F1 改为标准 `2TP/(2TP+FP+FN)=688/758`，每次分层 bootstrap 重算 micro F1，95% CI 为 `[0.888889, 0.926454]`。
- 原 6 条 citation contract 告警的证据实际在 `RAG_RETRIEVAL` trace；扩展来源提取并用原 observation 离线重建后违规为 `0`，没有重跑 Provider。该时点起答案语义质量进入独立双人盲审，后续封存与仲裁状态见 2026-08-24 记录。
- 生成客服 v2 新增 60 条 draft：20 个 intent 各 3 条，hard 35，高风险 9，应转人工 16。双人盲标 sheet 已就绪，仲裁前不与 v1 合并或报分。
- 执行 10 条 Search hard-negative 真实成对回放：Recall/MRR/NDCG 前后 delta 均为 `0`，硬约束违规 `0`；仍保留 `23/34/47` 三个多商品/集合意图/比较对象难例，未改 qrels 或 v9 final。
- 客服 HTTP 本地 P50/P95/P99 为 `1014.1/15212.5/60141.6 ms`；token `114720/6649`，32 次 Provider call 中 31 次未定价、1 次缺 usage，因此 `costCny=null`。这些是本地诊断，不是生产 SLO。
- 验收：新增专项与 manifest 联合回归 `41 passed`，Python 全量 `1279 passed/7 skipped`；随后在真实 MySQL 上补跑 migration `8 passed`，Java 26 模块 Maven reactor `BUILD SUCCESS`。

### 2026-08-23：槽位质量与本地容量限制收敛

- 在冻结的 60 条 HUMAN_VERIFIED gold 上扩展确定性槽位抽取：brand/budget/feature/兼容型号/排除条件/受众/数量等进入结构化 entities；paired replay 的 full-slot Span F1 `0.907652 -> 0.996364`、EM `0.558824 -> 0.911765`，修复 12 case、回归 0，残余 `009/020/058` 为严格金额格式差异。
- 意图分类固定关闭 thinking；新增严格整句纯社交短路，业务混合句不会命中。纯社交直接走 deterministic workflow，Episode 明确记录 `deterministicSocialReply/llmSkipped/ragSkipped/sideEffectAllowed`；可选 fast-support 生成模式默认关闭。
- 统一 usage 语义：有 token 无可信价格为 `UNPRICED`，Provider 调用无 usage 为 `MISSING_USAGE`，审计确认无 LLM 调用才是 `NOT_APPLICABLE/no_llm_call`；费用未知始终保持 `null`。
- 新增 `benchmark-capacity`：固定 HUMAN_VERIFIED 只读 case，按并发输出 QPS、P50/P95/P99、stage、usage、资源和 badcase，答案只存 hash/长度；所有包声明 `notProductionSlo=true` 且不进入质量分母。
- 同 4 case、每档 8 请求的 v2/v4 对照中，v4 `32/32`；c2/c4/c8 QPS `0.433/0.371/0.429 -> 0.694/0.684/0.905`，LLM 路径 P95 `15.015/19.912/18.404s -> 9.129/11.541/8.800s`。c1 P95 `14.852s -> 24.666s`，外部 Provider 波动如实保留，不能概括成全档改善。
- 纯社交最终审计探针 v4 为 `5/5`，P50/P95/P99 `629.6/716.1/726.6ms`，Provider calls/token `0`，每条 observation 均含 `deterministicSocialReply=true`。启动器重启动态层后因端口预占探测自动迁移到 Agent `7092`、Worker `7093`；评测器通过 `run/runtime.env` 正确跟随。

### 2026-08-24：重启复核、容量证据升级与成本口径收敛

- 重启后 readiness 复核通过：MySQL、Redis、RabbitMQ、Worker、Java Gateway、MCP 和 Elasticsearch 1024 维向量 mapping 全部正常；Python 固定使用 Conda `shop`。
- 新增 Agent 单次生成 hard deadline `AGENT_LLM_CALL_DEADLINE_SECONDS=45`，与 graph/Worker `120s` 总 deadline 分层。超时保留 `LLM_TIMEOUT`、trace 和 `MISSING_USAGE`，不伪造成功。
- 容量 benchmark v5 加入 warm-up `4`（不进分母），正式 4 case × 并发 `1/2/4/8` × 每档 `20`，共 `80/80` 安全执行；QPS `0.396/0.641/0.965/1.353`，混合 P50/P95/P99（c8）`1051.5/10505.0/12032.6ms`，LLM 路径 P95 `10.211/9.852/10.574/12.013s`，badcase `0`。证据包 `capacity-readonly-v5-20260824`，仍明确 `notProductionSlo=true`。
- 清理客服 HTTP 旧 v1 单人评分实现和旧空表；v2 双人盲审、封存、第三人仲裁成为唯一答案语义主线。双人封存后仍必须完成独立第三人仲裁，才可发布答案正确率、引用支持率、转人工适当率和 unsafe-answer rate。
- 新增带 URL、抓取时间、区域、模型 fingerprint 和页面 SHA-256 的目录价估算证据：`qwen3.7-plus` 北京 ≤256K 原价输入/输出 `2/8 CNY per 1M tokens`。状态固定为 `ESTIMATED_LIST_PRICE`，不改运行时 `UNPRICED`，不启用费用门禁；真实合同/账单仍未知。
- 相关单测、manifest checker、compileall 和 readiness 已通过；v5 比 v4 样本更大但仍是共享本机/外部 Provider 描述性诊断，不能声称严格因果或生产容量提升。
- 客服 HTTP 最终答案的 60 条双盲已由独立第三人完成 `8` 条仲裁，发布 `HUMAN_REVIEWED_ADJUDICATED` 只读包。双人四标签案件级一致 `52/60=0.866667` 是标注可靠性；最终质量为答案正确 `51/60=85.0%`（95% CI `73.9%–91.9%`）、引用语义支持 `6/30=20.0%`（`9.5%–37.3%`）、转人工适当 `60/60`、unsafe `0/60`、联合质量 `32/60=53.3%`。该结果揭示动态事实来源未绑定的主缺口，`releaseGateEligible=false`，不追溯修改 v9 final。

### 2026-08-24：客服边界修复、人工标签审计口径校正

- 复用并 fail-closed 加载已封存人工数据：`gold-v1-human-adjudicated.jsonl`（60 条，SHA-256 `112dfd...ddc527`）和固定 HTTP 答案 labels；标签不能套用到新答案，源 run/answer hash 任一变化即拒绝。
- 重新核查标注合理性：案件级全字段一致 `35/60`，但 intent 字段 `57/60`、Cohen κ `0.945913`，risk `56/60`、κ `0.889908`，handoff `60/60`；低案件一致主要是 slots `45/60` 的 schema/金额格式差异，不应写成“意图只有 58.3% 正确”。
- 规则预路由在同一 60 条 HUMAN_VERIFIED gold 上重跑：Intent Macro-F1 `0.955299 -> 1.000000`（`011/044/057` 修复）、风险字段不一致 `7 -> 0`；高风险 Recall `10/10`、handoff Recall `14/14`、critical miss `0/6` 保持。该结果是同集优化回放，不是新 holdout 泛化证据。
- 具体修复：无订单的退款时效改走 `REFUND` 政策回答，已有订单/退款上下文才走 `REFUND_STATUS`；同类“这款商品和另一款哪个好”保留咨询对象并澄清，跨品牌/跨品类比较仍走搜索；无商品卡的属性咨询专家不再获得 `SEARCH_PRODUCTS`，避免泛搜索；状态变更型退款/取消/确认收货/改地址/售后提案标为 MEDIUM 风险。
- 搜索约束回归：显式排除词、手机壳/兼容机型类别和 category+exclusion 同时过滤已通过 `79` 个定向测试；未重刷 Search final 或 qrel。
- 审计报告现在同时记录 `UNVERIFIABLE_RUNTIME_FACT=24` 与其中人工判“回答正确”的 `19` 条，并保留所有 case ID；前者是证据不足，不是模型错误率。旧 HTTP 回放仍为答案正确 `51/60`、引用支持 `6/30`，新 HTTP 输出必须重新双盲审查。

### 2026-08-24：人工标签合理性复核与证据传播修复

- 新增 `客服标注审计`：60 条中 59 个槽位为 `DIRECT_SPAN`，1 个为有“一个”原文依据的 `DERIVED_ALLOWED_RULE`（`cs-gold-v1-055`），0 个槽位无法由输入解释；未发现可由原文直接证伪的明确误标。
- 将标注风险拆成可复核边界：退款政策/状态（011/057）、商品比较（044）、支付方式 taxonomy（049）、售后换货（056）、履约/物流风险（007/008）和确认收货策略（014）。这些是规范选择或 taxonomy 缺口，不把仲裁标签包装成专家真值。
- 修复动态业务工具 `sourceRefs` 从 Java/MCP 结果到最终 HTTP envelope 的传播，并保留只有负查询证据时的引用；增加回归测试。旧 HTTP 引用支持 `6/30=20.0%` 仍是不可变历史观察，修复后必须新 run、新双盲审，不能直接推算提升。
- `shop` 环境定向回归 `53 passed`，标注审计/人工数据专项 `10 passed`；全量 pytest 在最终验证阶段重新执行，真实 MySQL migration 未连接时仅条件跳过。
- 联网调研 BEIR、Elastic RRF、Microsoft RAG Evaluators、Ragas、Self-RAG、AgentBench/ToolBench、NIST AI RMF；结论写入质量报告：AI_Shop 重点展示硬约束电商搜索、动态权威快照、客服路由/转人工、确认写入和幂等，不复制 InsightVault 的文档 RAG 主线。

### 2026-08-24：v13 HTTP 回放、评测器假阳性与新盲审准备

- 发现 API/Worker 重启并不会自动更新独立 MCP 进程；曾出现“源码已修复、MCP 仍运行旧 `product_service.py`”的定向回放现象。新增 API、Worker、MCP 共同 source fingerprint，`/health/ready` 和 evaluation preflight 不一致即 fail-closed。
- 动态订单、商品、库存、报价、优惠券、物流、退款、评价、工单及负查询结果现在由 Java/MCP 权威 `sourceRefs` 传到 graph state、Episode/step、response verifier 和 HTTP envelope；`matched=false` 仅代表本次定义范围内未命中，禁止解释为平台范围不存在。
- `customer-service-http-v13-20260824` 对冻结 60 条执行一次真实 Provider HTTP observation：完整终态 `60/60`、HTTP error `0`、行为契约 `10/10`、hard-constraint violation `0`；Provider calls `18`、input/output tokens `78,470/5,486`、`costCny=null` / `UNPRICED`，本地 P50/P95/P99 `1015.049/11372.651/22858.230 ms`。这些是执行、路由、契约和本地诊断，不是人工最终答案质量。
- 原始 observation（report SHA `46916af...e805`）保存为只读 `customer-service-http-v13-pre-evaluator-fix-20260824`。`001/002/034/042` 的“不能据此断言平台无货”被旧纯正则错误当成“平台无货”断言；新增免责声明感知检测和正反两条回归测试。正式 v13 包（report SHA `2b1b97...94357`）只从同一原始 observation 做确定性离线重算，`providerCallsReexecuted=false`，因此四条行为契约误报的消失不是模型质量提升。
- v13 `answerQuality.status=PENDING_HUMAN_REVIEW`，所有人工质量分数保持 `null`。已生成两份独立随机顺序、无 expected/预测标签的 60 条盲审表（SHA `3e4fdb...85fa6` / `e6476c...884aa`）；必须完成双人填写、封存和第三人仲裁后，才能报告 v13 答案正确率、引用语义支持率、转人工适当率、unsafe-answer rate、联合质量及新 badcase。历史 v1 `51/60`、`6/30`、`32/60` 继续只绑定旧 run。
- 定向回归 `33 passed`、Ruff、`compileall`、`git diff --check` 通过；v13 原始 observation 与正式 rebuilt 包均采用只读 `0444` 和 `SHA256SUMS`。本轮又将 manifest 验证扩展为同时校验原始 observation、正式 rebuild、开放盲审表、report hash 与只读属性。
- 额外保留 v11/v12 成对运行版本排错证据：同一 10 条定向集在旧 MCP 仍加载旧代码时出现 `6/10` 行为契约违例，完整重启后的恢复对照为 `0/10`。manifest 现在校验二者的只读文件集、SHA-256、相同 HUMAN_VERIFIED 数据哈希、预期状态和违例数；它们不进入质量分母。v5/v10 两个被 v13 覆盖的 60 条中间运行，以及绑定 v10 且未填写的 v3 盲审草稿已物理删除，避免把过期输出误当成当前主线。清理后 manifest、文档一致性、Ruff、`compileall` 与全量 Python 回归均通过：`1392 passed, 7 skipped`；跳过项只依赖真实 MySQL 8 migration 环境。

### 2026-08-24：v13 双盲封存、分歧诊断与仲裁准备

- 用户提供根目录两份 v13 标注表；逐条校验 60/60 case、四项标签、sourceRefs、答案哈希和 report SHA-256，未重跑 Provider，也未修改 v13 observation。
- 两份 sealed 表的案件级完全一致为 `49/60=81.67%`；字段级一致：answerCorrect `59/60`、citationSupport `50/60`（Cohen κ `0.735450`）、handoff `59/60`、unsafe `60/60`。这是一致性/标注可靠性，不是模型质量率。
- 11 条仲裁 badcase 为 `004/005/006/007/008/009/014/016/017/035/012`。前 10 条都围绕动态订单快照未直接支持回答中的商品名或流程风险/资格断言；`012` 还涉及取消订单能否确定拒绝及是否应转人工。完整双人备注和冻结 sourceRefs 位于待仲裁 evidence package。
- 生成只读包 `customer-service-http-v13-answer-review-pending-adjudication-20260824`，包含两份 sealed 原件、agreement、`SHA256SUMS` 和 11 条模板；项目根目录保留第三人填写文件 `adjudication.answer-review-v13.open.jsonl`。历史 v1 `51/60`、`6/30`、`32/60` 不迁移到 v13，等待独立仲裁后才计算新输出的答案质量。
- 修复评测工具文档硬编码旧 v2 仲裁文件名的问题：说明现在引用实际导出的 adjudication 路径，并增加回归断言。仓库内 v13 open 表恢复为空白模板，已标注输入逐字节保存在 sealed package，不重复进入当前主线。

### 2026-08-24：v13 第三人仲裁完成与质量坏例固化

- 第三人填写的 11 条 JSONL 先由合并器验证：case 集合、冻结答案、`sourceRefs`、双评原件、source report SHA-256、标签枚举和独立 reviewer ID 全部一致；未重跑 Provider。pending parent package 与新 final package 均通过 `SHA256SUMS` 验证。
- 生成只读 `customer-service-http-v13-answer-review-adjudicated-20260824`：状态 `HUMAN_REVIEWED_ADJUDICATED`、双评案件一致 `49/60=81.67%`、第三人仲裁 `11` 条。最终质量为答案正确 `59/60=98.33%`（Wilson `[0.911449,0.997052]`）、引用语义支持 `20/34=58.82%`（`[0.422216,0.736340]`）、转人工适当 `59/60=98.33%`、unsafe `0/60`（上界 `6.02%`）、联合 `46/60=76.67%`（`[0.645637,0.855604]`）。这是冻结 60 条 HTTP 回放，不是 CSAT/FCR/生产成功率，也不进入 release gate。
- 14 条 badcase 已逐 case 固化：`004/005/006/008/014/016/017/035` 缺订单项或商品名等动态详情证据；`007/009` 缺工具能力或操作后果证据；`018/019/055` 缺售后资格规则；`012` 同时是取消订单结论过度确定、引用不足和转人工边界错误。下一轮必须把这些 case 变为回归，而不是靠放宽 `SUPPORTED` 定义消除。
- 原 report 里的 `PENDING_HUMAN_REVIEW` 是生成时字段，不能覆盖；外部 final evidence 才是人工质量生命周期。v13 与历史 v1 答案、证据传播和 eligible 引用分母不同，禁止写成严格 A/B；免责声明评测器修复也仍只代表评测器正确性修复。

### 2026-08-25：v20 真实回放、双盲与第三人仲裁完成

- 在四项 regression preflight ready 后完成多轮定向修复和真实 HTTP 复验；最终 `customer-service-http-v20-20260825` 源码指纹为 `736cad91...a26`，执行 `60/60`、行为契约 `11/11`、handoff accuracy `1.0`、hard constraint violation `0`、fixture cleanup failure `0`。本地 P50/P95/P99 `1011.116/2010.016/7268.746 ms` 仅是 `LOCAL_FULL_STACK_NOT_PRODUCTION_SLO`，cost 保持 `UNPRICED`。
- 最后两项高价值修复将无具体订单线索的通用退款政策问法从订单解析转到 RAG，并将“退款/退货 + 条件/资格/规则/政策/要求”映射到 canonical fact `aftersales.request_and_refund_boundary`。没有降低全局 evidence threshold；受影响回归 `321 passed`，Ruff、`compileall` 和 `git diff --check` 通过。
- 两名真人 reviewer 的 60 条 sealed 表案件级一致 `56/60=93.33%`；4 条分歧 `014/029/043/059` 由独立 `reviewer-c` 仲裁。最终只读包 `customer-service-http-v20-answer-review-adjudicated-20260825` 状态为 `HUMAN_REVIEWED_ADJUDICATED`，答案正确 `57/60=95.00%`、引用支持 `25/36=69.44%`、转人工适当 `60/60`、unsafe `1/60`、联合质量 `49/60=81.67%`。
- 11 条 final badcase 按原标签保留：`008/019/020/021/027/055` 缺工单能力或提交后果证据；`009/014` 缺退款/确认后果证据；`018` 已匹配订单却错误声称未定位；`029/043` 有商品/优惠/排序或 Android 硬约束证据缺口。`014` 同时是答案、引用和 unsafe badcase，是继续开发时的第一优先级。
- v20 源 HTTP report 的 `PENDING_HUMAN_REVIEW` 仍是不可变生成时字段；外部 final package 才表示人审完成。v13/v20 可以作为不同冻结输出的描述性观察，不能只挑联合或引用上升而隐去答案正确与 unsafe 变化，也不能称严格因果 A/B。

### 2026-08-26：v27 完整 HTTP 回放、双盲与仲裁完成

- 针对 v20 后的动态事实、对象保留和引用边界修复，完成 60 条真实 HTTP 输出的 v27 冻结 replay。源 run 为 `customer-service-http-v27-full-quality-fixes-eval-contract-20260826`，完整源 report SHA-256 为 `f8724dac5c951b30a046dbe30ad3a4ce65b2a60a935aaf42a5720406fb172a61`；源文件在被 `.gitignore` 的 `run/`，最终 immutable package 以该 hash 绑定。
- 两名 reviewer 独立盲审案件级一致 `58/60`，2 条由独立 `reviewer-c` 仲裁。最终答案正确 `59/60=98.33%`（Wilson `[0.911449,0.997052]`），可计分引用支持 `35/36=97.22%`（`[0.858303,0.995080]`），联合质量 `59/60=98.33%`，unsafe `0/60`（95% 上界约 `6.02%`）。这些是人工语义结果；案件一致率仅是审查可靠性，行为契约全通过不进入主质量分母。
- 当前唯一 badcase 为 `cs-gold-v1-001`：具体型号“索尼 WH-1000XM6”在回答摘要中丢失，来源只支持完整型号未命中，不能支持扩大的品牌/预算结论。修复后必须新 HTTP run、新双盲和仲裁，不能改写 v27 包；v20 的 `014/018/043` 保留为历史回归素材。
- 中央 `docs/evidence-manifest.json` 新增 v27 descriptor 和完整 package/CI/hash 校验；scorecard 与所有当前入口改为 v27 主质量入口。v27 明确 `releaseGateEligible=false`、不是 unseen holdout、不是线上准确率。
- 非线上剩余查漏补缺固定为：源码/历史 badcase 隔离的新 unseen holdout、客服 claim-level 独立人工评审、隐私保护 HTTP slot typed/hash projection、Search `23/34/47` 的 query decomposition paired A/B，以及独占环境 steady-state/stress/soak。规则回放、门禁全通过、标注一致率和共享本机容量只作诊断。

### 2026-08-26：v2 有效性审计、生产路由修复与 v43 全链路复验

- 将 v1 60 条与 additions 60 条的历史人工结果组装为 hash-bound canonical package；120 条数据 SHA-256 为 `ab5129a73cf6f986173d92e3f5f04ab7e8689bae9ad4c7d7294fa13b587ee079`。sealed review、26 条仲裁和合并投影均可验证，但 reviewer-a 填写后的 OPEN bytes 缺失、A/B export hash 字段语义错误且无独立性声明，故状态保持 `HUMAN_VERIFIED_PROVENANCE_REVIEW_REQUIRED`、`releaseGateEligible=false`。
- 新增 taxonomy v2.1 与跨 case label consistency audit：`RECOMMENT` 的生产语义是订单追评写提案，不是推荐 refinement；后者归 `PRODUCT_SEARCH`。同时发现 amount raw span、budget 完整性、quantity occurrence 和复合 productName/feature 政策分裂。共 5 项发现、25 条受影响、3 项 blocking，已生成不含 gold/model 的独立重仲裁模板，旧 120 条 immutable 不原地改写。
- 修复推荐/搜索/追评、退款政策/状态、否定和人工接管边界；修复 verified order/RAG/写提案编排、forced read-to-write、物流停滞和缺失退款引用的确定性 owned-record 解析。退款状态没有可核验订单时现在保守澄清，不调用 LLM 猜到账状态，也不执行写工具。
- 修正评测执行口径：observation capture 与 `adapterStatus=PASSED` 生产成功分离；人工接管只统计生产 HUMAN/HANDOFF，不再把内部 specialist handoff 算成功。HTTP 与离线指标都继承 label/provenance audit，行级 `HUMAN_VERIFIED` 不再自动成为 validity gate。
- 同一暴露 120 条 paired 结果为 intent accuracy `0.741667 -> 0.975`（改善 28、回退 0，McNemar `p≈1e-8`）、handoff recall `0.6875 -> 1.0`（10/0，`p=0.00195312`）、Intent Macro-F1 `0.717240 -> 0.962857`（Δ `0.245617`，bootstrap 95% CI `[0.180408,0.334601]`）、raw slot F1 `0.773920 -> 0.982481`（Δ `0.208560`）。这些只证明 development fix；标签门禁和 unseen 泛化仍未通过。
- 最新 `customer-service-http-v43-human-v2-routing-execution-fix-20260826` 在本地 Java/MySQL/Agent/Worker/MCP/RAG/Provider 路径完成 observation `120/120`、生产 episode `120/120`、人工接管 TP/TN/FP/FN `32/88/0/0`、行为契约 `22/22`、hard constraint `0`、fixture cleanup failure `0`。本地 P50/P95/P99 `623.195/6161.334/7795.156 ms`，20 次 Provider、token `64606/2698`、费用 `UNPRICED/null`；不是生产 SLO。
- v43 的两份 120 条独立随机空白答案盲审表已绑定 report SHA-256 `c8efc7d69e4e85f1478c74c6365f749b36842168f8e08cb3f86a2244704498d3`。当前人审覆盖 `0/120`，答案正确、引用语义支持和 unsafe 全部保持 `null`；`013/068/115` 的安全降级仅证明 fail-safe 生效，不代表答案正确。
- 新增 binary-safe dirty-worktree source-freeze 创建/验证工具：tracked changes 保存相对 HEAD 的完整 patch，untracked regular files 只记录 path/size/SHA-256 而不复制潜在敏感 bytes。全面结论和外部人工门槛见 [评测体系全面审计](../evaluation/AI-Shop评测体系全面审计与执行结果-20260826.md) 与 [人工交接总清单](../evaluation/人工标注交接总清单-20260826.md)。

### 2026-08-27：v43→v54→v56 badcase 修复与人工复评收口

- v43 完成 A/B 120 条审批与 3 条仲裁，联合质量 `105/120`、unsafe `1/120`；15 条 badcase 被固化为回归合同。v54 修复后人工联合质量为 `113/120`，剩余 7 条 badcase。
- v55 在 7 条已知难例上完成 `7/7` 定向回放；v56 以新冻结输出完成 `120/120` 生产路径执行、`29/29` 行为契约与引用结构违例 `0`。生成时 report 仍保留 `PENDING_HUMAN_REVIEW`，人工语义结果由独立证据包承载。
- v56 A/B 案件级完全一致 `118/120`，`cs-gold-v1-026`与 `cs-candidate-v2-096` 由第三人仲裁。最终答案正确 `120/120`（Wilson 95% 下界 `96.90%`）、引用语义支持 `67/67`（下界 `94.58%`）、转人工 `120/120`、unsafe `0/120`（上界 `3.10%`）、联合质量 `120/120`、badcase `0`。
- 人工拥有最终决策权，AI 只辅助文字与落盘；证据口径为 `HUMAN_APPROVED_AI_ASSISTED`、`humanDecisionAuthority=true`、`aiAssistanceUsed=true`、`pureHumanUnaidedClaim=false`。原始回传和最终包分别独立封存、哈希绑定。
- v43→v54→v56 只是开发者已见 120 条上的描述性修复链。v56 明确 `normalQualityDenominatorExcluded=true`、`releaseGateEligible=false`、`finalUnseenEligible=false`，不证明 unseen 泛化、线上安全率或 CSAT/FCR。

## 关键踩坑、排错与效果

下表只把同一数据、同一 observation 或同一候选规模的结果称为“前后对比”。不同 final 使用不同 dataset/source hash，只能作为排错生命周期，不能包装成严格 A/B。

| 问题 | 排查结论与处理 | 前后效果 | 陈述边界 |
|---|---|---|---|
| 60 条 draft 自评全部 `1.0` | draft 标签来自构造过程，不能当人工真值；改为双人盲标、25 条仲裁并冻结 HUMAN_VERIFIED 包 | 人工结果为 Intent Macro-F1 `0.955299`、full-slot F1 `0.907652`、EM `0.558824` | 这是证据可信度修正，不是模型质量下降 |
| Slot F1/CI 统计量不一致 | F1 统一为 `2TP/(2TP+FP+FN)`；bootstrap 每次按 `intent×risk×handoff` 抽完整 case 并重算统计量 | 点估计仍为 `0.907652`；展示由 `344/414` 改为 `688/758`，CI 由 `[0.884563,0.961881]` 修正为 `[0.888889,0.926454]` | 同一预测重算，不能称质量提升 |
| HTTP 槽位跨脱敏边界评分 | Episode 已将订单号等值脱敏，无法与原始 gold 做 span 比较 | HTTP Slot F1/EM 从错误可评分假设改为 `UNAVAILABLE`；规则预路由 slot 指标单独保留 | `UNAVAILABLE` 比伪造 0/1 更可信 |
| HTTP 引用结构出现 6 条告警 | 最终 envelope 未复制 `sourceRefs`，但 `RAG_RETRIEVAL` trace 保存了实际来源；从原 observation 离线重建 | citation contract invalid `6 -> 0`，Provider 未重跑 | 只修复引用链路提取，不等于答案语义正确 |
| HTTP 引用结构为 0 仍可能不可信 | 双盲+第三人仲裁把“结构上有来源”与“来源足以支持具体回答”分开度量 | 引用语义支持仅 `6/30=20.0%`，联合质量 `32/60=53.3%`；24 条 citation badcase 被保留 | 不能以 60/60 执行、citation contract 0 或双人一致率包装为 grounded-answer 质量 |
| 独立 MCP 进程可加载旧源码 | Worker/API 重启不等于 MCP reload；对 API、Worker、MCP 增加共同 source fingerprint，并在 readiness/preflight 做一致性校验 | 版本不一致从可静默发生改为 fail-closed | 是部署一致性修复，不能以此改写任何历史 HTTP 标签 |
| 免责声明被评测器误杀 | 旧正则把“不能据此断言平台无货”视作“平台无货”断言；改为提取未被否定覆盖的独立 claim，并保存命中文本 | 同一 v13 observation 的 4 条 `NO_UNSUPPORTED_CATALOG_ABSENCE_CLAIM` 假阳性消失；Provider 未重跑 | 是评测器正确性修复，不是模型由失败提升为通过 |
| v13 证据传播修复后不能继承旧人审分数 | 新 output 的 sourceRefs、答案和运行 hash 均不同；生成双人 blind sheet 并绑定 report/source-observation hash | v13 人工质量保持 `PENDING_HUMAN_REVIEW`，而非把旧 `85%/20%/53.3%` 迁移过去 | 必须完成新双盲和仲裁后，才能宣称任何 v13 答案质量指标 |
| Search 难例可能被“修指标”掩盖 | 固定 v9 qrels/ranking，真实 Provider 对 10 条难例做 paired replay | Recall/MRR/NDCG delta 全为 `0`，约束违规 `0`；`23/34/47` 仍失败 | 结论是无回归，不是质量提升 |
| 候选快照存在 N+1 风险 | 同一隔离 MySQL、同一 `100` 候选比较 batch/N+1，并校验结果等价与 rollback | offer P50 `89.805 -> 23.864 ms`，降低 `73.4%`；decision P50 `70.501 -> 2.405 ms`，降低 `96.6%`；round trip `100 -> 1` | 本地受控 benchmark，不是生产 SLO |
| Provider usage/费用容易被记成零 | usage 缺失记 `MISSING_USAGE`，无可信价格记 `UNPRICED`，费用保持 `null` | 消除“未知成本=0”的错误结论 | 尚不能给出单请求成本门禁 |
| 完整人工 slot schema 覆盖不足 | 在生产预路由补充已冻结 schema 的确定性实体抽取，并用同 gold paired replay | Span F1 `0.907652 -> 0.996364`；EM `0.558824 -> 0.911765`；12 fixed、0 regressed | 同集优化证据，不是新 holdout；3 个金额 raw-format badcase 保留 |
| 客服属性/比较误路由与风险级别偏低 | 增加同类比较识别、无卡咨询工具隔离、退款政策/状态上下文分流，并将状态变更提案提升为 MEDIUM | 同 60 条 gold Intent Macro-F1 `0.955299 -> 1.000000`；风险不一致 `7 -> 0`；定向路由/约束回归 `257` 相关测试通过 | 同集规则回放；HTTP 最终答案和引用指标必须用新运行重新盲审，不能继承旧 labels |
| Agent 长尾缺少容量分层 | 新增只读完整链路并发 benchmark、warm-up 和 hard deadline，分开 deterministic 与 LLM path、stage 和 usage | v5 warm-up `4/4`、正式 `80/80`；c8 QPS `1.353`、LLM P95 `12.013s`；社交 path 5 次均零 Provider | 共享本机、外部 Provider，仍不是 steady-state、stress/soak 或生产 SLO |
| “零 token”与“usage 缺失”混淆 | 从 Episode 的 LLM_CALL/AGENT_POLICY 事实判定是否实际调用 Provider | 纯社交由 `MISSING_USAGE (0 missing) -> NOT_APPLICABLE/no_llm_call` | 只有可审计确认未调用时才能使用 NOT_APPLICABLE |

历史 final 的排错趋势保留在 immutable archive：v3 的 Search/RAG/Agent case pass 为 `42/50、36/50、16/25`，Agent `pass^8=0.60`、critical power `0.50`；v5 为 `50/50、47/50、25/25` 和 `0.92/0.833`；v8 为 `50/50、49/50、24/25` 和 `0.96/0.833`；v9 为 `50/50、50/50、25/25` 和 `1.0/1.0`。主要暴露过 Provider 不完整、RAG injection/no-answer、Episode 未终态、confirmation actionToken 缺失、state diff 和重复试验隔离问题。由于各版本 holdout/source hash 不同，这组数值只能说明 fail-closed 排错逐步收敛，不能用于声称同集质量提升。

## 方法成熟度判断

| 能力 | 判断 | 依据与边界 |
|---|---|---|
| 混合商品检索 | 方法成熟，项目规模仍小 | BM25 + 向量 + RRF/rerank + Java offer snapshot；有 qrel 排名指标，但只有 47 商品和小样本 |
| 生成式导购 | 适合作为受控解释/澄清层 | 候选和交易事实不交给 LLM 生成；没有行为序列、ranker、A/B，不能称工业个性化推荐 |
| 可信 RAG | 工程路径成熟 | 版本/注入隔离、最小证据、引用、拒答；当前 lexical 指标不是人工语义真值 |
| 业务 Agent | 可靠执行边界清晰 | 确认后 Java 写入、幂等、未知结果和终态校验；客服理解已有 60 条 HUMAN_VERIFIED 离线金标 |
| 评测发布 | 证据纪律较完整 | current/archive、SHA-256、数据互斥、客服人工金标、HTTP 答案第三人仲裁、容量诊断和 fail-closed；仍缺真实行为、引用修复后的新 holdout 与生产压测 |

与传统搜索和前沿生成式搜推的取舍：型号、数字、品牌、否定词必须保留倒排/结构化硬约束；向量用于同义泛化；LLM 只在候选集内做
澄清、比较和 grounded explanation。直接让 LLM 生成商品 ID 会把幻觉、库存和报价时变性带入交易边界，不适合当前项目。

## 与 InsightVault 的差异化

InsightVault 侧重深文档 RAG 的证据召回、引用和消融；AI_Shop 不重复堆同一套 RAG 排行榜，主展示电商/客服特有的多目标 Search、
硬约束、订单权威状态、确认写入、幂等、人工转接和故障恢复。客服四项金标也比通用文档问答指标更贴近本项目岗位叙述。

## 当前边界与下一步

- 客服 v2.1 标签和 v56 答案均是人工最终决策、AI 辅助编辑的已见开发证据；release gate 仍显式关闭。
- v56 在当前已见集上已无人工 badcase；下一项客服质量工作不是继续重刷同集，而是由独立保管者生成开发者未见、预注册的新 holdout，一次性执行后再双盲+仲裁。
- 完成 v2 来源独立复核的全 60 条扩展与保管声明；当前来源审计 slot exact `0.50` 未达 `0.70` 门槛，因此 intent/slot/handoff 仍只作开发诊断。
- 每次修改都要保留指标级 badcase、输入/gold/prediction、根因和新旧数据集哈希；Search hard negative 继续只做 paired replay，不改标签刷分。
- Search 成对回放基线已固定；后续只针对 `23/34/47` 做 query decomposition/对象保留的可见集 A/B，不能改标签或重刷 final 制造提升。
- 当前容量曲线只证明本地短时只读诊断可运行；固定独占环境的 warm-up、steady-state、stress/soak 和 Provider 分层完成前，不新增生产 SLO 结论。
- 当前真实账单价格和 endpoint 合同仍未核验；目录价估算仅用于面试中的成本量级说明，不能写成实际单请求成本。
- 真实曝光/点击/购买、授权/合规数据到位前，不新增 CTR/CVR/GMV、CSAT/FCR 或单位经济性结论；v56 的全通过仅支持当前已见回放的可信性，不支持绝对安全或未来输出泛化主张。

### 2026-08-29：E57 客服真实 HTTP/LLM 证据刷新

- 在本地完整 Java/MySQL/Agent/Worker/MCP/RAG/Provider 栈上重新执行 v2.1 HUMAN_APPROVED_AI_ASSISTED 集合 `120/120`，并将脱敏 observation 确定性重建为只读 `customer-service-http-e57-evidence-refresh-20260829` 包；包内 `SHA256SUMS` 校验通过，`providerCallsReexecuted=false`。
- 运行事实：行为契约 `29/29`、引用结构违规 `0`、规则预路由 intent Macro-F1 `1.0`、高风险召回 `13/13`、handoff `29/29`；Provider 调用 `11`，费用 `UNPRICED/null`，本地延迟 P50/P95/P99 `617.989/1289.224/8880.928 ms`，明确不是生产 SLO。
- 新包仍为 `EXECUTED_PENDING_HUMAN_ANSWER_REVIEW`，不继承 v56 的人工答案分数；答案语义、unseen 泛化、线上 SLO、CSAT/FCR 和成本门禁均未由此刷新。详见 [E57 证据刷新](AI-Shop-客服E57真实运行证据刷新-20260829.md)。

### 2026-08-29：E57 购物导购真实浏览器链

- 在显式配置同源 origin 的本地全栈上完成 mobile Chromium `1/1`：真实推荐商品卡 → `reportClick`（`productId/requestId` 一致）→ 商品详情 → checkout 归因字段 → 加购记录（`aiRequestId/aiPosition/aiSource`）→ 清理购物车。
- trace 原件含会话 cookie，移到工作区外受限 quarantine，SHA-256 为 `ffe489507890375048aaddb515c7d8b370ac2d6b6d32487d3f0b2cede4179483`；不入 Git。支付段默认关闭，不能据此声称下单、支付或 GMV。
- 该用例本次走确定性 `SEARCH_PRODUCTS` 路径，不能替代 LLM 生成质量评测；客服真实 Provider 结果见 [客服 E57](AI-Shop-客服E57真实运行证据刷新-20260829.md)。
- 同日公开 regression `regression-e57-evidence-refresh-20260829` 通过 `20 Search/26 RAG/5 Agent` 全部硬门；Search recall@10 `0.958333`、nDCG@10 `0.993110`，RAG citation/grounded `1.0`，但这些仍是可见 regression 诊断，不能替代缺失的 v9 private holdout。

### 2026-08-29：E57 客服真实浏览器提案与选择链

- 在同一真实本地全栈上补跑退款提案与售后候选选择两条 mobile Chromium 用例，`2/2` 通过：退款请求停在 `ACTION_CONFIRM/REFUND`，售后选择停在 `CREATE_SUPPORT_CASE` 提案；重复选择返回相同 `messageId`，冲突选择返回 `409`。
- 两条用例均未确认退款或创建工单；trace 含会话 cookie，已移至工作区外受限 quarantine，哈希和边界见 [客服真实浏览器 E57 证据](AI-Shop-客服真实浏览器E57证据-20260829.md)。该结果补强浏览器/状态机证据，不刷新人工答案质量、CSAT/FCR、unseen 或生产 SLO。

### 2026-08-30：A1 发布事实 Alias 路由与探索评测

- 在生产 query planner 中复用发布版 fact metadata，将明确术语 alias/完整 fact ID 路由到既有 `factHints`；歧义和普通未标记提及 fail-closed，混合意图与语义缓存维度已补回归。实现提交为 `db26d43`，Text2SQL 和正式 unseen 均未改动。
- 真实外部模型 v4 完整执行 Search 50 / RAG 50 / Agent 25（Agent 200 trials），总门仍 `FAILED`。RAG case pass `29/50 → 31/50`、source coverage `0.586207 → 0.965517`，但 NDCG@5 `0.910388 → 0.858217`，generation/claim/citation 仅 `0.62`，不能概括为全面质量提升。
- 生命周期源码暴露门拒绝了与回归测试完整重合的 `agent-unseen-116`；没有绕过。探索副本只改写该条措辞并标记不可同题比较，RAG 50 条不变。正式资产五个哨兵 SHA 前后完全一致。
- 外置 evidence、candidate 与 runtime quarantine 的路径、SHA、逐 case 差异和后续边界见 [A1 事实 Alias 路由与探索评测](AI-Shop-A1事实Alias路由与探索评测-20260830.md)。
