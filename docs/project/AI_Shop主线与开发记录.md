# AI_Shop 主线与开发记录

> 当前发布：`release-20260822-ai-quality-v9` / `final-20260822-ai-quality-v9`
> 最近更新：2026-08-22（Asia/Shanghai）
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
但不等于已经证明 CTR、CVR、GMV、生产容量或人工客服准确率。

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
- 修复并回归验证支付扣款否定、重复/未授权支付、退款未到账、隐私数据与账号安全、否定转人工、破损短句和商品名 span 边界；当前 provisional 点估计为 Intent Macro-F1 `1.000`、高风险 Recall `1.000`、slot span F1 `1.000`、slot EM `1.000`、handoff Recall `1.000`，共 60 条、当前 0 badcase，95% CI 仍较宽。
- 额外修复首轮“支付方式有哪些”误建议转人工：`ASK_CLARIFICATION` 现在按低风险信息问法直接回答；第二次低置信度提示、连续第三次升级的既有契约保持不变。客服专项回归 `102 passed`，全量 Python `1244 passed, 7 skipped`（7 项均为真实 MySQL 8 migration 条件跳过）。
- 暂不修复 Search hard negative、不重刷 Search 质量数据；先保留真实漏召回作为后续 paired replay 素材。

## 方法成熟度判断

| 能力 | 判断 | 依据与边界 |
|---|---|---|
| 混合商品检索 | 方法成熟，项目规模仍小 | BM25 + 向量 + RRF/rerank + Java offer snapshot；有 qrel 排名指标，但只有 47 商品和小样本 |
| 生成式导购 | 适合作为受控解释/澄清层 | 候选和交易事实不交给 LLM 生成；没有行为序列、ranker、A/B，不能称工业个性化推荐 |
| 可信 RAG | 工程路径成熟 | 版本/注入隔离、最小证据、引用、拒答；当前 lexical 指标不是人工语义真值 |
| 业务 Agent | 可靠执行边界清晰 | 确认后 Java 写入、幂等、未知结果和终态校验；客服理解 F1 仍需人工金标 |
| 评测发布 | 证据纪律较完整 | current/archive、SHA-256、数据互斥和 fail-closed；缺人工复核、真实行为和生产压测 |

与传统搜索和前沿生成式搜推的取舍：型号、数字、品牌、否定词必须保留倒排/结构化硬约束；向量用于同义泛化；LLM 只在候选集内做
澄清、比较和 grounded explanation。直接让 LLM 生成商品 ID 会把幻觉、库存和报价时变性带入交易边界，不适合当前项目。

## 与 InsightVault 的差异化

InsightVault 侧重深文档 RAG 的证据召回、引用和消融；AI_Shop 不重复堆同一套 RAG 排行榜，主展示电商/客服特有的多目标 Search、
硬约束、订单权威状态、确认写入、幂等、人工转接和故障恢复。客服四项金标也比通用文档问答指标更贴近本项目岗位叙述。

## 当前边界与下一步

- 客服 gold 当前全为 `DRAFT_NEEDS_HUMAN_REVIEW`，报告状态是 `PROVISIONAL_NOT_HUMAN_GOLD`，不能进入发布门禁。
- 首先请人工复核 60 条标签（尤其产品咨询/搜索、退款政策/进度、隐私与账号安全、投诉严重度和破损短句），再固定 `HUMAN_VERIFIED` 版本；当前结果仍是 draft，不进入 release gate。
- 复核后只修复高价值客服 badcase，并对受影响切片回归：Intent Macro-F1、风险 Recall、slot F1/EM、handoff Recall。
- Search hard negative 只做记录；有时间再做同一数据集的 paired replay，不能用改标签或重刷结果制造提升。
- 真实曝光/点击/购买、人工盲评、授权/合规和容量数据到位前，不新增 CTR/CVR/GMV 或生产 SLO 结论。
