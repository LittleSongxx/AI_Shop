# AI_Shop 主线与开发记录

> 当前发布：`release-20260822-ai-quality-v9` / `final-20260822-ai-quality-v9`
> 最近更新：2026-08-23（Asia/Shanghai）
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

## 方法成熟度判断

| 能力 | 判断 | 依据与边界 |
|---|---|---|
| 混合商品检索 | 方法成熟，项目规模仍小 | BM25 + 向量 + RRF/rerank + Java offer snapshot；有 qrel 排名指标，但只有 47 商品和小样本 |
| 生成式导购 | 适合作为受控解释/澄清层 | 候选和交易事实不交给 LLM 生成；没有行为序列、ranker、A/B，不能称工业个性化推荐 |
| 可信 RAG | 工程路径成熟 | 版本/注入隔离、最小证据、引用、拒答；当前 lexical 指标不是人工语义真值 |
| 业务 Agent | 可靠执行边界清晰 | 确认后 Java 写入、幂等、未知结果和终态校验；客服理解已有 60 条 HUMAN_VERIFIED 离线金标 |
| 评测发布 | 证据纪律较完整 | current/archive、SHA-256、数据互斥和 fail-closed；缺人工复核、真实行为和生产压测 |

与传统搜索和前沿生成式搜推的取舍：型号、数字、品牌、否定词必须保留倒排/结构化硬约束；向量用于同义泛化；LLM 只在候选集内做
澄清、比较和 grounded explanation。直接让 LLM 生成商品 ID 会把幻觉、库存和报价时变性带入交易边界，不适合当前项目。

## 与 InsightVault 的差异化

InsightVault 侧重深文档 RAG 的证据召回、引用和消融；AI_Shop 不重复堆同一套 RAG 排行榜，主展示电商/客服特有的多目标 Search、
硬约束、订单权威状态、确认写入、幂等、人工转接和故障恢复。客服四项金标也比通用文档问答指标更贴近本项目岗位叙述。

## 当前边界与下一步

- 客服 gold 当前主线为 `HUMAN_VERIFIED`；release gate 仍显式关闭，避免把离线人工金标误写成线上成功率。
- 先修复/回归 `011/044/057` 的意图边界和 `055/058` 的 canonical 槽位/金额归一化；扩展 `brand/budget/feature` 等字段需先完成生产 schema 设计。
- 每次修改都要保留指标级 badcase、输入/gold/prediction、根因和新旧数据集哈希；Search hard negative 继续只做 paired replay，不改标签刷分。
- Search hard negative 只做记录；有时间再做同一数据集的 paired replay，不能用改标签或重刷结果制造提升。
- 真实曝光/点击/购买、人工盲评、授权/合规和容量数据到位前，不新增 CTR/CVR/GMV 或生产 SLO 结论。
