# AI_Shop 质量评测与 Badcase

> 当前证据：`release-20260822-ai-quality-v9` / `final-20260822-ai-quality-v9`
> 统计环境：Conda `shop`；所有延迟为本地完整链路观测，不是生产 SLO。

## 先看口径

质量指标回答“结果好不好”，门禁回答“是否允许发布”。门禁必须 100%，不作为优展示；Search 排名、客服理解和 RAG 证据质量才是
面试中应重点陈述的数值。每个质量指标都保留分母、95% 区间和 badcase，不能因为 runtime `PASS` 就认为没有质量坏例。

## 当前主质量结果

| 域/指标 | 点估计 | 分子/分母 | 95% CI（scorecard） | 质量 badcase |
|---|---:|---:|---|---:|
| Search Recall@10（macro/query） | 0.962121 | 44 query | [0.916667, 1.000000] percentile bootstrap | 3 query / 4 商品 |
| Search Recall@10（micro/qrel） | 0.928571 | 52/56 | [0.830246, 0.971874] Wilson | 4 漏召回商品 |
| Search MRR@10 | 0.937500 | 44 query | [0.880682, 0.988636] percentile bootstrap | 5 query |
| Search NDCG@10 | 0.920521 | 44 query | [0.868709, 0.964456] percentile bootstrap | 10 query |
| RAG grounded faithfulness | 1.000000 | 50/50 | [1.000000, 1.000000] percentile bootstrap | lexical/claim 下界，无语义人工真值 |
| RAG citation support | 1.000000 | 50/50 | [1.000000, 1.000000] percentile bootstrap | 无；semantic judge 只作 shadow |
| AI 客服 intent/slot/handoff | 见下节 | 独立 60 条双人盲标+仲裁 gold | bootstrap/Wilson | 3 intent、15 full-slot badcase |

Search/RAG/Agent 的运行 summary 也保存了独立固定种子的 bootstrap 区间；由于运行 summary 与 scorecard 是两次独立重采样，NDCG 和 P95 的区间端点可能有小幅差异，点估计、分母和 badcase 必须一致。这里的表格明确标注为 scorecard 区间，不能把两套端点混写成一个“线上置信区间”。

### 数值可用性判断

| 证据 | 当前可用程度 | 可以怎么说 | 不能怎么说 |
|---|---|---|---|
| Search 50 条离线 qrel | **可用于简历/面试** | `n=50`、44 条有 qrel 的 Recall/MRR/NDCG、95% CI、切片和漏召回/错排 case | 线上 CTR、个性化推荐收益或生产 SLO |
| 客服 60 条 HUMAN_VERIFIED | **可用于简历/面试** | 双人盲标、25 条 lead 仲裁后的 intent Macro-F1、风险 Recall、slot F1/EM、handoff Recall | 线上客服准确率、CSAT/FCR；当前规则预路由也不是完整 HTTP Agent |
| RAG 50 条、Agent 25 条 | **工程诊断可用** | RAG 检索/引用/拒答下界，Agent 幂等、终态和状态 diff 契约 | 人工语义准确率、开放世界 Agent 成功率 |
| 本地延迟、token、DB、故障矩阵 | **复盘/设计证据可用** | 环境、样本、P50/P95/P99、usage unknown、batch/N+1 和恢复契约 | 生产容量、费用为 0、线上 SLO |

门禁通过（`50/50`、`25/25`、`pass^8`、安全与终态为 100%）只是发布前置条件，不是质量提升分数；质量指标必须和 badcase 一起展示。

### Search slice（不加权掩盖失败）

| slice | case/judged | Recall@10 | MRR@10 | NDCG@10 | badcase |
|---|---:|---:|---:|---:|---|
| exact-model-number-brand | 10/10 | 1.0000 | 1.0000 | 1.0000 | 无 |
| chinese-synonym-oral | 10/10 | 1.0000 | 0.9500 | 0.9693 | `search-fin-v9-11-office` |
| budget-structured | 8/8 | 0.9167 | 0.9062 | 0.8880 | `search-fin-v9-23-snack-100`, `search-fin-v9-28-lip-100` |
| negative-exclusion | 8/8 | 0.9375 | 0.9375 | 0.8710 | `search-fin-v9-33-coat-no-outdoor`, `search-fin-v9-34-snack-no-wangwang` |
| no-result-conflict | 6/0 | 不可得 | 不可得 | 不可得 | 无 qrel，不能把门禁当质量分数 |
| fallback-partial-provider | 4/4 | 1.0000 | 0.8750 | 0.8712 | `search-fin-v9-43-partial-headset`, `search-fin-v9-44-partial-office` |
| category-brand-comparison | 4/4 | 0.8750 | 0.8750 | 0.8130 | `search-fin-v9-47-compare-xm`, `search-fin-v9-49-compare-lip`, `search-fin-v9-50-compare-home` |

已确认的 Search hard negative：多商品/多品牌 conjunction、否定约束候选不足、比较对象过早收窄。本阶段只记录，不修复、不重刷 final。

漏召回的可复核明细：`search-fin-v9-23-snack-100` 漏 `303019597302892`（可口可乐组合）和
`438316828084252`（芒果奶糕），`search-fin-v9-34-snack-no-wangwang` 漏 `303019597302892`，
`search-fin-v9-47-compare-xm` 漏 `350000232815799`（索尼十周年版）。排序 badcase 主要是
`search-fin-v9-11-office`（办公主机相关性靠后）、`28`（预算口红目标排第 4）、`33`（排除户外后仍有无关类目靠前）、
`43/44`（partial provider 下相关商品排序靠后）和 `49/50`（比较型请求混入无关类目或目标次序偏后）；逐商品返回列表仍以 current `bad-cases.jsonl` 为准。

### 本地延迟诊断（不是 SLO）

| 域 | P50 | P95 | P99 | 长尾 badcase |
|---|---:|---:|---:|---|
| Search（50） | 269.6 ms | 797.3 ms | 940.6 ms | `search-fin-v9-31-lip-no-chanel`, `search-fin-v9-17-matte-lip`, `search-fin-v9-36-sony-no-xm6` |
| RAG（50） | 1825.4 ms | 4246.7 ms | 4525.3 ms | `rag-fin-v9-31-mail-memory`, `rag-fin-v9-28-drone-street`, `rag-fin-v9-49-grounding-term` |
| Agent（25） | 1362.5 ms | 17077.8 ms | 20943.0 ms | `agent-fin-v9-10-memory-policy`, `agent-fin-v9-12-price-policy` |

P95/P99 样本均少于 100，只作本地长尾定位；不能写成生产容量或 SLO。

## AI 客服四项关键证据

完整逐 case 结果见 [客服金标评测](customer-service/客服金标评测.md)，机器证据见 [客服 JSON](customer-service/客服金标评测.json)。这是
生产 `resolve_intent(..., allow_llm=False)` 规则预路由基线，不是完整 HTTP Agent；标签已完成双人盲标和 lead reviewer 仲裁，状态为
`HUMAN_VERIFIED`，但仍是离线证据且不自动进入 release gate。数据集 60 条，覆盖商品咨询/搜索边界、支付风险、隐私、否定、少件和槽位格式。

| 指标 | 当前值 | 分子/分母 | 95% CI | badcase |
|---|---:|---:|---|---:|
| Intent Macro-F1 | 0.955299 | 19.105978/20 intents | [0.642094, 0.926190] | `011,044,057`：政策/比较边界 |
| 高风险 intent Recall | 1.000000 | 10/10 | [0.722467, 1.000000] | 无 |
| Slot entity/span F1（完整人工 schema） | 0.907652 | 344/414 chars | [0.884563, 0.961881] | 15；扩展槽位/归一化/漏抽 |
| 请求级 Slot Exact Match（完整人工 schema） | 0.558824 | 19/34 non-empty-slot cases | [0.394539, 0.711165] | 同上 |
| Handoff Recall | 1.000000 | 14/14 | [0.784689, 1.000000] | 无 |
| 严重漏转人工率 | 0.000000（越低越好） | 0/6 critical | [0.000000, 0.390334] | 无 |

`intentMacroF1` 的 `[0.642094, 0.926190]` 是冻结包中的 case-bootstrap 诊断区间。由于 20 个 intent 标签稀疏，percentile 区间可能不包含点估计 `0.955299`；当前只把点估计和逐 case badcase 用于面试描述，不把该区间当作总体泛化保证。后续若扩充标签，改用按 intent 分层 bootstrap，并生成新数据集版本，不覆盖本包。

关键 badcase 和根因素材：

- `cs-gold-v1-011` / `057`：退款政策/到账时效分别被路由到 `REFUND_STATUS`、`CHAT`，taxonomy 与政策/状态边界不一致。
- `cs-gold-v1-044`：比较型问题被判为 `PRODUCT_SEARCH`，应进入商品咨询/比较路径。
- `cs-gold-v1-001/002/003/029/033/034/041/042/043/045/059`：人工仲裁保留 `brand/budget/feature/兼容型号` 等扩展槽位，生产 extractor 尚未映射；不应全部归因于模型漏抽。
- `cs-gold-v1-009/020/058`：金额币种/单位归一化差异；`cs-gold-v1-055`：canonical `productName/quantity` 仍未抽出。

生产 canonical slot 投影（`orderId/orderItemId/productId/productName/amount`）为 Span F1 `0.992785`、EM `0.882353`，作为 schema 对齐诊断，不替换完整人工 schema 主指标。所有指标均保留逐 case 输入、gold、prediction 和根因；当前不能外推为线上客服成功率、CSAT 或 FCR。

最近一次修复了首轮低风险“支付方式有哪些”误建议转人工的策略边界；人工闭环和 canonical 诊断专项测试已通过，完整测试结果以 CI 输出为准。

## RAG 与 Agent 边界

- RAG 保留 lexical claim、事实 ID、引用支持和 no-answer 作为安全下界；semantic shadow judge 记录 prompt/model/provider/claim/证据和
  disagreement，但未完成校准前不进入门禁，不称人工准确率。Final `50/50` available、`0` disagreement；development `18/18`
  available 但有 `1` disagreement，regression `25/26` available 且有 `1` unavailable，均必须保留在报告中。
- Agent `pass^5`/`pass^8`、tool routing、终态、state diff、幂等和重复副作用是可靠性门禁，不是客服 intent F1，也不是开放世界成功率。
- 当前 Agent final 25 条、200 trials 的 `pass^8=1.0`、retry idempotency `22/22`、duplicate side effect `0` 只说明冻结任务集中的声明契约满足；本报告的 60 条客服 HUMAN_VERIFIED gold 才测理解质量。

RAG answerable retrieval Recall@5 为 `29/29=1.000000`（Wilson `[0.883030, 1.000000]`）；injection resistance 为 `8/8=1.000000`（Wilson `[0.675592, 1.000000]`），样本小，不能外推到开放知识库。

## 故障恢复矩阵（独立辅助证据）

证据包：[fault-v9-20260822](../../AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/resilience/fault-v9-20260822/)。
共执行 `12` 个场景：生产边界 HARD `11` 个，`11/11` recovery contract 通过；harness boundary SHADOW `1` 个，`1/1`
通过，`allContractsPassed=true`。覆盖 embedding、BM25、vector、rerank、Java 商品/库存快照、LLM malformed、Redis checkpoint、
worker deadline、MCP 失败和重复请求。故障场景从正常质量分母排除；current final 的 `resilienceMetrics.status=NOT_RUN` 是正常 run
没有注入故障的明确状态，不得把辅助矩阵改写成 final 的普通质量通过率。

## DB batch/N+1 证据（独立 benchmark）

证据包：[db-benchmark-v9-20260822](../../AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/db/db-benchmark-v9-20260822/)。
真实隔离数据库、候选规模 `1/10/50/100`、结果等价和 rollback probe 均通过。100 候选时 batch offer/decision feature 分别为
`1` 次 round trip、P50 `23.864/2.405 ms`；N+1 分别为 `100` 次 round trip、P50 `89.805/70.501 ms`，错误率为 `0`。
这是证明避免 N+1 的本地描述性证据，不是线上容量或 SLO；完整 P95 和各规模数据见 benchmark report。

## 必须 100% 的发布契约

Search hard constraint/no-result/provider completeness、RAG invalid citation/严重安全/runtime error、Agent terminal state/state diff/重复副作用/
runtime safety error 必须全通过；任何失败阻断发布。不要用这些通过率替代主质量指标。

## Usage、成本和 DB 证据

Provider 未返回 usage 记 `MISSING_USAGE`；无可信单价时 `costCny=null`，不写成零。v9 final usage 汇总为：Search `121` 次调用、`121`
次 `MISSING_USAGE`；RAG `107` 次调用、token `67,267/10,115`（输入/输出）、单价未配置；Agent `99` 次调用、token
`271,154/36,108`，其中 `2` 次 `MISSING_USAGE`、其余为 `UNPRICED`。因此没有费用硬门禁。隔离 MySQL benchmark 在候选 `1/10/50/100`
比较 batch 与 N+1：100 候选时 batch 1 次 round trip、N+1 100 次，错误率 0、rollback probe 通过；这是本地描述性 benchmark，不是线上容量/SLO。

## 证据与复现

```bash
conda activate shop
cd AI_Shop-backend/AI_Shop-agent
python -m evaluation.cli validate
python -m evaluation.cli slices --split development
python -m evaluation.cli scorecard --output /tmp/aishop-scorecard.md --json-output /tmp/aishop-scorecard.json
python -m evaluation.cli customer-service-gold --mode rule
# 客服人工金标已完成；新版本仍必须按同一 fail-closed 流程 seal/compare/merge
python -m evaluation.cli customer-service-review export --annotator reviewer-a --output /tmp/reviewer-a.open.jsonl
```

current 只指向 v9 final；v2 是历史通过 archive，v3-v8 是 immutable failed archive；旧运行不参与当前分母。机器索引、哈希和生命周期见
[evidence-manifest.json](../evidence-manifest.json)。

## 当前不做的外推

没有真实曝光/点击/购买、客服满意度盲评、生产并发、支付合规或长期线上实验，因此不能声称 CTR/CVR/GMV、工业级个性化推荐、生产 SLO、CSAT/FCR
或开放世界客服成功率。后续只做高价值补证：

1. 用同一 60 条 gold 跑完整 HTTP Agent/LLM 路径，并把 rule pre-router 与最终客服响应分开计分；优先回归 `011/044/057` 意图边界和 `055/058` canonical 槽位/金额归一化。
2. 扩充客服 gold 到至少 100–200 条，并按 intent/risk/handoff 分层 bootstrap；再做独立人工答案正确性、证据引用和转人工盲评，避免用规则自评替代人工真值。
3. 对 Search hard negative 做同标签 paired replay，记录召回/排序变化和新增 badcase，不修改 v9 final；RAG 只补少量人工 claim-level 校准样本，继续把 semantic judge 保持 shadow。
4. 若需要性能结论，再在固定环境做并发/负载曲线（QPS、错误率、P50/P95/P99、资源瓶颈）并保留 provider usage/价格表；在此之前所有本地延迟与成本仍是诊断值。
