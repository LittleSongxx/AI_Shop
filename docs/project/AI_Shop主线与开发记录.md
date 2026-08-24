# AI_Shop 主线与开发记录

> 当前发布：`release-20260822-ai-quality-v9` / `final-20260822-ai-quality-v9`
> 最近更新：2026-08-24（Asia/Shanghai）
> Python 评测环境：Conda `shop`

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
- 客服 HTTP 最终答案的两份 60 条盲审已完整封存：四标签案件级完全一致 `52/60=0.866667`，`8` 条分歧已冻结为只读待仲裁包；answer correctness 与 citation support 的一致性分别为 `54/60`（κ `0.446154`）和 `58/60`（κ `0.942857`），handoff/unsafe 均为 `60/60`。这些是标注可靠性而非模型质量率，最终指标仍为 `PENDING_ADJUDICATION`。

## 关键踩坑、排错与效果

下表只把同一数据、同一 observation 或同一候选规模的结果称为“前后对比”。不同 final 使用不同 dataset/source hash，只能作为排错生命周期，不能包装成严格 A/B。

| 问题 | 排查结论与处理 | 前后效果 | 陈述边界 |
|---|---|---|---|
| 60 条 draft 自评全部 `1.0` | draft 标签来自构造过程，不能当人工真值；改为双人盲标、25 条仲裁并冻结 HUMAN_VERIFIED 包 | 人工结果为 Intent Macro-F1 `0.955299`、full-slot F1 `0.907652`、EM `0.558824` | 这是证据可信度修正，不是模型质量下降 |
| Slot F1/CI 统计量不一致 | F1 统一为 `2TP/(2TP+FP+FN)`；bootstrap 每次按 `intent×risk×handoff` 抽完整 case 并重算统计量 | 点估计仍为 `0.907652`；展示由 `344/414` 改为 `688/758`，CI 由 `[0.884563,0.961881]` 修正为 `[0.888889,0.926454]` | 同一预测重算，不能称质量提升 |
| HTTP 槽位跨脱敏边界评分 | Episode 已将订单号等值脱敏，无法与原始 gold 做 span 比较 | HTTP Slot F1/EM 从错误可评分假设改为 `UNAVAILABLE`；规则预路由 slot 指标单独保留 | `UNAVAILABLE` 比伪造 0/1 更可信 |
| HTTP 引用结构出现 6 条告警 | 最终 envelope 未复制 `sourceRefs`，但 `RAG_RETRIEVAL` trace 保存了实际来源；从原 observation 离线重建 | citation contract invalid `6 -> 0`，Provider 未重跑 | 只修复引用链路提取，不等于答案语义正确 |
| Search 难例可能被“修指标”掩盖 | 固定 v9 qrels/ranking，真实 Provider 对 10 条难例做 paired replay | Recall/MRR/NDCG delta 全为 `0`，约束违规 `0`；`23/34/47` 仍失败 | 结论是无回归，不是质量提升 |
| 候选快照存在 N+1 风险 | 同一隔离 MySQL、同一 `100` 候选比较 batch/N+1，并校验结果等价与 rollback | offer P50 `89.805 -> 23.864 ms`，降低 `73.4%`；decision P50 `70.501 -> 2.405 ms`，降低 `96.6%`；round trip `100 -> 1` | 本地受控 benchmark，不是生产 SLO |
| Provider usage/费用容易被记成零 | usage 缺失记 `MISSING_USAGE`，无可信价格记 `UNPRICED`，费用保持 `null` | 消除“未知成本=0”的错误结论 | 尚不能给出单请求成本门禁 |
| 完整人工 slot schema 覆盖不足 | 在生产预路由补充已冻结 schema 的确定性实体抽取，并用同 gold paired replay | Span F1 `0.907652 -> 0.996364`；EM `0.558824 -> 0.911765`；12 fixed、0 regressed | 同集优化证据，不是新 holdout；3 个金额 raw-format badcase 保留 |
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
| 评测发布 | 证据纪律较完整 | current/archive、SHA-256、数据互斥、客服人工金标、容量诊断和 fail-closed；仍缺真实行为、最终仲裁后的 HTTP 答案金标和生产压测 |

与传统搜索和前沿生成式搜推的取舍：型号、数字、品牌、否定词必须保留倒排/结构化硬约束；向量用于同义泛化；LLM 只在候选集内做
澄清、比较和 grounded explanation。直接让 LLM 生成商品 ID 会把幻觉、库存和报价时变性带入交易边界，不适合当前项目。

## 与 InsightVault 的差异化

InsightVault 侧重深文档 RAG 的证据召回、引用和消融；AI_Shop 不重复堆同一套 RAG 排行榜，主展示电商/客服特有的多目标 Search、
硬约束、订单权威状态、确认写入、幂等、人工转接和故障恢复。客服四项金标也比通用文档问答指标更贴近本项目岗位叙述。

## 当前边界与下一步

- 客服 gold 当前主线为 `HUMAN_VERIFIED`；release gate 仍显式关闭，避免把离线人工金标误写成线上成功率。
- 由独立第三人完成客服 HTTP 答案的 `8` 条分歧仲裁，再报答案正确率、引用支持率、转人工适当性和 unsafe-answer rate。
- 完成 v2 新增 60 条的双人盲标/仲裁后，生成 120 条 HUMAN_VERIFIED v2 并重算分层 CI；当前 draft 不可报分。
- 每次修改都要保留指标级 badcase、输入/gold/prediction、根因和新旧数据集哈希；Search hard negative 继续只做 paired replay，不改标签刷分。
- Search 成对回放基线已固定；后续只针对 `23/34/47` 做 query decomposition/对象保留的可见集 A/B，不能改标签或重刷 final 制造提升。
- 当前容量曲线只证明本地短时只读诊断可运行；固定独占环境的 warm-up、steady-state、stress/soak 和 Provider 分层完成前，不新增生产 SLO 结论。
- 当前真实账单价格和 endpoint 合同仍未核验；目录价估算仅用于面试中的成本量级说明，不能写成实际单请求成本。
- 真实曝光/点击/购买、最终 HTTP 答案仲裁和授权/合规数据到位前，不新增 CTR/CVR/GMV、CSAT/FCR 或单位经济性结论。
