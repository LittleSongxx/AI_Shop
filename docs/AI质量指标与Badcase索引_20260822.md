# AI_Shop 质量主指标与 Badcase 索引（v9）

> 这份报告是从不可变 v9 evidence 派生的诊断视图。`PASSED` 只说明契约门禁满足，不能推出质量满分；每个主指标都给出分母、95% CI 和 badcase。

## 证据边界

- run/release：`final-20260822-ai-quality-v9` / `release-20260822-ai-quality-v9`
- dataset SHA-256：`d02e89e644fc115e55c5553baeb98681178822d3cc2e7395003b7cfc3bb5cd07`
- evidence `SHA256SUMS` SHA-256：`94edeb894f3e2d36f597cb9cc9796d6e0e6f6b81da08f030b8723435e441c11a`
- holdout：`125` 条，未写入 Git；scorecard 不修改 current。
- 置信区间：二项比例使用 Wilson；宏平均与 P95 使用 percentile bootstrap。样本少于 100 的 P95 只作本地描述性观察。

## 一页结论

- Search 的主要短板不是执行失败，而是多目标召回与排序：micro Recall@10 为 `52/56`，有 3 个 query、4 个相关商品未召回；另有 8 个 query 的 MRR/NDCG 低于理想排序。
- RAG 的 lexical/引用/拒答证据在冻结集上没有坏例，但有 3 次 query expansion Provider failure，均安全 fallback；它是客服事实安全证据，不应与 InsightVault 的深度文档 RAG benchmark 重复包装。
- Agent 的当前样本没有工具参数、终态或重复副作用坏例，但长尾主要集中在 RAG/政策路径；客服 intent Macro-F1、slot F1、转人工 Recall 仍未被独立标注测量。

## Search 主质量指标

| 指标 | 值 | 分子/分母 | 95% CI | badcase 数 | badcase IDs |
|---|---:|---:|---:|---:|---|
| Recall@10 macro/query | 0.9621 | —/44 | [0.9167, 1.0000] | 3 | search-fin-v9-23-snack-100, search-fin-v9-34-snack-no-wangwang, search-fin-v9-47-compare-xm |
| Recall@10 micro/qrel | 0.9286 | 52.0/56 | [0.8302, 0.9719] | 3 | search-fin-v9-23-snack-100, search-fin-v9-34-snack-no-wangwang, search-fin-v9-47-compare-xm |
| MRR@10 macro/query | 0.9375 | —/44 | [0.8807, 0.9886] | 5 | search-fin-v9-11-office, search-fin-v9-28-lip-100, search-fin-v9-33-coat-no-outdoor, search-fin-v9-44-partial-office, search-fin-v9-49-compare-lip |
| NDCG@10 macro/query | 0.9205 | —/44 | [0.8687, 0.9645] | 10 | search-fin-v9-11-office, search-fin-v9-23-snack-100, search-fin-v9-28-lip-100, search-fin-v9-33-coat-no-outdoor, search-fin-v9-34-snack-no-wangwang, search-fin-v9-43-partial-headset, search-fin-v9-44-partial-office, search-fin-v9-47-compare-xm, search-fin-v9-49-compare-lip, search-fin-v9-50-compare-home |

`Recall@K macro/query` 与已发布 summary 保持兼容；`Recall@10 micro/qrel` 额外回答“总共漏掉了几个相关商品”。两者不能混称。
R@3/R@5 和本地 P95 仍保留在 JSON scorecard 中用于定位与回归，但不作为项目优展示。

### Search hard negatives

- `search-fin-v9-23-snack-100`（budget-structured）：100元以内旺旺雪饼和可乐零食
  - 期望相关商品：065293686460191, 303019597302892, 438316828084252
  - 实际 Top10：065293686460191
  - 漏召回：303019597302892 可口可乐（Coca-Cola）可乐*12+雪碧*8+芬达*4 有糖汽水 碳酸饮料 330ml*24罐；438316828084252 芒果奶糕牛扎芒果干之恋草莓奶糯酪条办公室解馋网红休闲零食小吃
  - 复盘假设：多商品/多品牌 conjunction 被过早收窄，或候选池没有保留足够的同类商品；需要在 query intent、召回扩展和比较对象保留策略上做 paired replay。
- `search-fin-v9-34-snack-no-wangwang`（negative-exclusion）：平价零食不要旺旺雪饼
  - 期望相关商品：303019597302892, 438316828084252
  - 实际 Top10：438316828084252
  - 漏召回：303019597302892 可口可乐（Coca-Cola）可乐*12+雪碧*8+芬达*4 有糖汽水 碳酸饮料 330ml*24罐
  - 复盘假设：多商品/多品牌 conjunction 被过早收窄，或候选池没有保留足够的同类商品；需要在 query intent、召回扩展和比较对象保留策略上做 paired replay。
- `search-fin-v9-47-compare-xm`（category-brand-comparison）：WH-1000XM6和十周年版降噪耳机如何比较
  - 期望相关商品：231335860060520, 350000232815799
  - 实际 Top10：231335860060520
  - 漏召回：350000232815799 索尼（SONY）WH-1000XX 十周年典藏版 头戴式无线耳机 降噪蓝牙耳机
  - 复盘假设：多商品/多品牌 conjunction 被过早收窄，或候选池没有保留足够的同类商品；需要在 query intent、召回扩展和比较对象保留策略上做 paired replay。

### Search 排序 badcase

- `search-fin-v9-11-office`：mrrAt10=0.5000；query=`想找一台稳定耐用的办公台式主机`；返回顺序 `995230446006541 > 650980987345712 > 301841010226518`，状态仍为 `PASSED`，所以它不会出现在 runtime `bad-cases.jsonl`。
- `search-fin-v9-11-office`：ndcgAt10=0.6934；query=`想找一台稳定耐用的办公台式主机`；返回顺序 `995230446006541 > 650980987345712 > 301841010226518`，状态仍为 `PASSED`，所以它不会出现在 runtime `bad-cases.jsonl`。
- `search-fin-v9-23-snack-100`：ndcgAt10=0.6735；query=`100元以内旺旺雪饼和可乐零食`；返回顺序 `065293686460191`，状态仍为 `PASSED`，所以它不会出现在 runtime `bad-cases.jsonl`。
- `search-fin-v9-28-lip-100`：mrrAt10=0.2500；query=`100元以内不沾杯雾面口红唇釉`；返回顺序 `298286497857602 > 843304724668395 > 763086281772264 > 664740861226404`，状态仍为 `PASSED`，所以它不会出现在 runtime `bad-cases.jsonl`。
- `search-fin-v9-28-lip-100`：ndcgAt10=0.4307；query=`100元以内不沾杯雾面口红唇釉`；返回顺序 `298286497857602 > 843304724668395 > 763086281772264 > 664740861226404`，状态仍为 `PASSED`，所以它不会出现在 runtime `bad-cases.jsonl`。
- `search-fin-v9-33-coat-no-outdoor`：mrrAt10=0.5000；query=`男士冬季棉服不要户外软壳`；返回顺序 `917186661226040 > 864824304719236 > 422543322296606 > 435499775288057 > 664740861226404 > 519183041848998`，状态仍为 `PASSED`，所以它不会出现在 runtime `bad-cases.jsonl`。
- `search-fin-v9-33-coat-no-outdoor`：ndcgAt10=0.6309；query=`男士冬季棉服不要户外软壳`；返回顺序 `917186661226040 > 864824304719236 > 422543322296606 > 435499775288057 > 664740861226404 > 519183041848998`，状态仍为 `PASSED`，所以它不会出现在 runtime `bad-cases.jsonl`。
- `search-fin-v9-34-snack-no-wangwang`：ndcgAt10=0.3374；query=`平价零食不要旺旺雪饼`；返回顺序 `438316828084252`，状态仍为 `PASSED`，所以它不会出现在 runtime `bad-cases.jsonl`。
- `search-fin-v9-43-partial-headset`：ndcgAt10=0.8340；query=`检索服务不完整时返回真实的索尼降噪耳机`；返回顺序 `350000232815799 > 231335860060520`，状态仍为 `PASSED`，所以它不会出现在 runtime `bad-cases.jsonl`。
- `search-fin-v9-44-partial-office`：mrrAt10=0.5000；query=`办公电脑供应商部分异常也不能编造商品`；返回顺序 `995230446006541 > 650980987345712 > 869004898763662 > 301841010226518`，状态仍为 `PASSED`，所以它不会出现在 runtime `bad-cases.jsonl`。
- `search-fin-v9-44-partial-office`：ndcgAt10=0.6509；query=`办公电脑供应商部分异常也不能编造商品`；返回顺序 `995230446006541 > 650980987345712 > 869004898763662 > 301841010226518`，状态仍为 `PASSED`，所以它不会出现在 runtime `bad-cases.jsonl`。
- `search-fin-v9-47-compare-xm`：ndcgAt10=0.7872；query=`WH-1000XM6和十周年版降噪耳机如何比较`；返回顺序 `231335860060520`，状态仍为 `PASSED`，所以它不会出现在 runtime `bad-cases.jsonl`。
- `search-fin-v9-49-compare-lip`：mrrAt10=0.5000；query=`水光唇釉和雾面哑光唇釉有什么不同`；返回顺序 `843304724668395 > 664740861226404 > 298286497857602 > 100766326868880 > 811128851953351 > 763086281772264`，状态仍为 `PASSED`，所以它不会出现在 runtime `bad-cases.jsonl`。
- `search-fin-v9-49-compare-lip`：ndcgAt10=0.6309；query=`水光唇釉和雾面哑光唇釉有什么不同`；返回顺序 `843304724668395 > 664740861226404 > 298286497857602 > 100766326868880 > 811128851953351 > 763086281772264`，状态仍为 `PASSED`，所以它不会出现在 runtime `bad-cases.jsonl`。
- `search-fin-v9-50-compare-home`：ndcgAt10=0.8340；query=`净水器和空气净化器分别适合什么场景`；返回顺序 `547755968243478 > 055216728343001`，状态仍为 `PASSED`，所以它不会出现在 runtime `bad-cases.jsonl`。

### Search slice 摘要

| slice | case/judged | Recall@10 | MRR@10 | NDCG@10 | badcase IDs |
|---|---:|---:|---:|---:|---|
| budget-structured | 8/8 | 0.9167 | 0.9062 | 0.8880 | search-fin-v9-23-snack-100, search-fin-v9-28-lip-100 |
| category-brand-comparison | 4/4 | 0.8750 | 0.8750 | 0.8130 | search-fin-v9-47-compare-xm, search-fin-v9-49-compare-lip, search-fin-v9-50-compare-home |
| chinese-synonym-oral | 10/10 | 1.0000 | 0.9500 | 0.9693 | search-fin-v9-11-office |
| exact-model-number-brand | 10/10 | 1.0000 | 1.0000 | 1.0000 | 无 |
| fallback-partial-provider | 4/4 | 1.0000 | 0.8750 | 0.8712 | search-fin-v9-43-partial-headset, search-fin-v9-44-partial-office |
| negative-exclusion | 8/8 | 0.9375 | 0.9375 | 0.8710 | search-fin-v9-33-coat-no-outdoor, search-fin-v9-34-snack-no-wangwang |
| no-result-conflict | 6/0 | 不可得 | 不可得 | 不可得 | 无 |

## RAG 最小事实安全证据（不扩张为第二套 RAG 主指标）

| 指标 | 值 | 分子/分母 | 95% CI | badcase 数 | badcase IDs |
|---|---:|---:|---:|---:|---|
| Grounded faithfulness | 1.0000 | 50.0/50 | [1.0000, 1.0000] | 0 | 无 |
| Citation support | 1.0000 | 50.0/50 | [1.0000, 1.0000] | 0 | 无 |
| No-answer accuracy | 1.0000 | 50.0/50 | [0.9287, 1.0000] | 0 | 无 |
| Retrieval Recall@5 | 1.0000 | 29.0/29 | [0.8830, 1.0000] | 0 | 无 |

### RAG Provider/尾延迟诊断 badcase

- `rag-fin-v9-28-drone-street`：无人机冷链能否覆盖到具体街道门牌？；query expansion failure=1，安全 fallback=True。
- `rag-fin-v9-31-mail-memory`：系统能否读取我的邮箱历史并永久保存？；query expansion failure=1，安全 fallback=True。
- `rag-fin-v9-44-policy-version`：历史订单状态规则能否替代当前版本规则？；query expansion failure=1，安全 fallback=True。
- Semantic shadow 只报告 availability/disagreement 和逐 claim 证据；当前没有人工校准，不能写成人工准确率或一致性。

## Agent 运行诊断（不是客服意图准确率）

| 指标 | 值 | 分子/分母 | 95% CI | badcase 数 | badcase IDs |
|---|---:|---:|---:|---:|---|
| Tool routing accuracy | 1.0000 | 25.0/25 | [0.8668, 1.0000] | 0 | 无 |
| Tool argument accuracy | 1.0000 | 25.0/25 | [0.8668, 1.0000] | 0 | 无 |
| Agent P95 latency | 17077.7783 | —/25 | [8745.0917, 22113.3039] | 2 | agent-fin-v9-10-memory-policy, agent-fin-v9-12-price-policy |

### Agent 长尾/失败诊断

- 当前没有 runtime/终态/状态 diff/重复副作用坏例；这属于必须满足的可靠性契约，不作为“质量提升”展示。

## 必须 100% 的契约门禁（不作为优展示）

| 域 | 门禁 | 观察值 | 分母 | 违规 badcase |
|---|---|---:|---:|---|
| search | noResultAccuracy | 50 | 50 | 无 |
| search | hardConstraintViolations | 0 | 50 | 无 |
| search | providerCompleteness | 50 | 50 | 无 |
| search | recomputeMismatch | 0 | 44 | 无 |
| rag | invalidCitation | 0 | 50 | 无 |
| rag | severeSafetyViolation | 0 | 50 | 无 |
| rag | runtimeError | 0 | 50 | 无 |
| agent | terminalStateCorrectness | 25 | 25 | 无 |
| agent | stateDiffMatch | 25 | 25 | 无 |
| agent | duplicateSideEffects | 0 | 25 | 无 |
| agent | runtimeOrSevereSafety | 0 | 25 | 无 |

这些门禁包括：Search 硬约束/Provider completeness/no-result，RAG invalid citation/严重安全/runtime error，Agent 终态/state diff/重复副作用/runtime 安全。门禁失败应阻断发布，但通过不等于推荐质量或客服理解质量达到行业满分。

## AI 客服垂类尚未测量的高价值指标

当前证据验证了工具契约和业务终态，但没有独立客服理解金标，因此不能声称以下准确率。秋招只补四项高价值指标：

- intent Macro-F1，并保留逐 intent Precision/Recall/F1 与 confusion matrix；
- 高风险意图 Recall 和严重漏判数（退款、取消、隐私、越权、紧急人工请求）；
- 订单号、商品、金额、时间等关键 slot 的 entity/span F1，以及请求级 slot Exact Match；
- handoff Recall 与严重漏转人工率。

下一轮只需先做一套小而独立的人工标注集：intent + slots + shouldHandoff + riskLevel。request mode 可作为 intent taxonomy 的属性，不另造一个主指标；不在秋招窗口扩张到情绪、风格、ECE/Brier、泛化 Answer Relevance 或模拟 CTR。

## 与 InsightVault 的差异化

InsightVault 的 `embodied-v1` 已规划 required/forbidden fact、gold retrieval/final-evidence recall、合法引用、消融和稳定性；其真实 run 输出尚未完整。AI_Shop 因此不重复堆一套深文档 RAG 排行榜，而聚焦电商/客服闭环：商品多目标召回与排序、硬约束、客服请求理解、人工转接、订单权威状态、确认和幂等写入。

## 后续路线（按投入产出比）

1. 先修 Search 的三类已证实 hard negative：多商品/多品牌、否定约束候选不足、比较对象过早收窄；每次修复只做 paired replay，主报 Recall@10、NDCG@10、MRR@10 与对应 badcase。
2. 用少量人工客服金标补 intent/slot/handoff/risk 四项；把每个错例连同模型输出、正确标签、根因和回归 ID 固化，不把当前 Agent pass^k 当意图准确率。
3. 在同一数据集上做一次 candidate recall -> rerank -> hard-filter 的小型消融，报告上述三个 Search 指标、P95 和 usage/cost unknown；没有明确假设就不加新指标。
4. 只保留 Worker/MQ redelivery、lease 失效、catalog/price/stock/version mutation replay 作为交易安全门禁维护，不继续把它们扩写成面试主指标。
5. 有真实曝光/点击/购买和授权数据后，才做 CTR/CVR/GMV、偏差校正和线上 A/B；当前禁止外推。

## 不能外推

本报告不能证明 CTR/CVR/GMV、工业级个性化推荐、生产容量/线上 SLO、支付合规、人工语义准确率或开放世界客服成功率。
