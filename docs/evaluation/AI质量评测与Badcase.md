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
| AI 客服 intent/slot/handoff | 见下节 | 独立 60 条双人盲标+仲裁 gold | bootstrap/Wilson | 3 intent、3 strict-format slot badcase |

Search/RAG/Agent 的运行 summary 也保存了独立固定种子的 bootstrap 区间；由于运行 summary 与 scorecard 是两次独立重采样，NDCG 和 P95 的区间端点可能有小幅差异，点估计、分母和 badcase 必须一致。这里的表格明确标注为 scorecard 区间，不能把两套端点混写成一个“线上置信区间”。

### 数值可用性判断

| 证据 | 当前可用程度 | 可以怎么说 | 不能怎么说 |
|---|---|---|---|
| Search 50 条离线 qrel | **可用于简历/面试** | `n=50`、44 条有 qrel 的 Recall/MRR/NDCG、95% CI、切片和漏召回/错排 case | 线上 CTR、个性化推荐收益或生产 SLO |
| 客服 60 条 HUMAN_VERIFIED | **可用于简历/面试** | 双人盲标、25 条 lead 仲裁后的 intent Macro-F1、风险 Recall、slot F1/EM、handoff Recall | 线上客服准确率、CSAT/FCR；HTTP 答案质量已双盲封存但仍待 8 条第三人仲裁，slot 仍是规则预路由口径 |
| RAG 50 条、Agent 25 条 | **工程诊断可用** | RAG 检索/引用/拒答下界，Agent 幂等、终态和状态 diff 契约 | 人工语义准确率、开放世界 Agent 成功率 |
| 本地延迟、容量、token、DB、故障矩阵 | **复盘/设计证据可用** | 环境、样本、并发/QPS、P50/P95/P99、usage unknown、batch/N+1 和恢复契约 | 持续生产容量、费用为 0、线上 SLO |

门禁通过（`50/50`、`25/25`、`pass^8`、安全与终态为 100%）只是发布前置条件，不是质量提升分数；质量指标必须和 badcase 一起展示。

### 当前达标与可用性

这里的“达标”指预先声明的项目离线门禁，不是统一行业认证或生产 SLO。

| 维度 | 判断 | 依据 | 对系统可用性的含义 |
|---|---|---|---|
| Search 相关性与约束 | **达标** | Recall@10 `0.9621`、MRR `0.9375`、NDCG `0.9205`，硬约束违规 `0` | 足以支撑当前 47 商品、小流量场景的可用搜索/导购；多对象和比较型难例仍需 fallback/澄清 |
| RAG 受控问答 | **有条件达标** | answerable Recall@5 `29/29`，lexical grounded/citation/no-answer 门禁通过 | 足以支撑封闭知识库 FAQ；没有独立人工语义真值，不能证明开放域答案准确率 |
| 客服理解与转人工 | **当前离线点估计达标** | Intent Macro-F1 `0.9553`、高风险和 handoff Recall `1.0`；full-slot F1 `0.9964`、EM `0.9118` | 可用于带澄清和人工兜底的客服路由；只有 60 条同集回放，写操作仍须 Java 校验和确认 |
| Agent 交易可靠性 | **冻结场景达标** | 25 case、200 trials，`pass^8=1.0`，state diff/终态全匹配，重复副作用 `0`；故障 contract `11/11 HARD + 1/1 SHADOW` | 支撑受控交易提案、确认和幂等执行；不等于开放世界 Agent 成功率 |
| 单请求交互延迟 | **Search 可用，生成链路长尾偏高** | 本地 Search P95 `0.80s`、RAG `4.25s`、Agent `17.08s`、客服 HTTP `15.21s`，客服 P99 `60.14s` | 搜索体验可用；Agent/客服需要流式反馈、超时降级和长尾优化，不能承诺生产 SLO |
| 容量与成本 | **已有扩大后的本地诊断，生产结论未建立** | 只读 4 case、warm-up `4`、正式 `80` 请求（并发 `1/2/4/8`、每档 20）；c8 `1.353 QPS`；生成路径 P95 `10.211–12.013s`；usage 有 token 但未定价 | 可定位并发瓶颈和回归，不能据此承诺持续吞吐、生产 SLO 或单位经济性 |

综合结论：当前证据足以支撑一个小商品集、低并发、有人工作为最终兜底的可用演示或受控预生产系统，也足以作为求职项目展示；尚不足以证明无人值守、高并发生产系统。主要限制是 HTTP 答案盲审仍有 8 条待第三人仲裁、Agent/客服生成链路长尾、共享本机负载仍非生产容量、真实账单价格未知，以及客服金标仍只有 60 条。

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

2026-08-23 已对其中 10 条已知难例做真实 Provider 成对回放，baseline/current 的 Recall@10 `0.833333`、micro Recall
`14/18=0.777778`、MRR@10 `0.725`、NDCG@10 `0.650292`，四项 delta 均为 `0`，硬约束违规 `0`。这证明当前代码无回归，
不是新 final 或质量提升证据；仍剩 `23/34/47` 三个多商品、集合意图和比较对象召回难例。证据包见
[search-hard-negative-paired-v1-20260823](../../AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/search/search-hard-negative-paired-v1-20260823/)。

### 本地延迟诊断（不是 SLO）

| 域 | P50 | P95 | P99 | 长尾 badcase |
|---|---:|---:|---:|---|
| Search（50） | 269.6 ms | 797.3 ms | 940.6 ms | `search-fin-v9-31-lip-no-chanel`, `search-fin-v9-17-matte-lip`, `search-fin-v9-36-sony-no-xm6` |
| RAG（50） | 1825.4 ms | 4246.7 ms | 4525.3 ms | `rag-fin-v9-31-mail-memory`, `rag-fin-v9-28-drone-street`, `rag-fin-v9-49-grounding-term` |
| Agent（25） | 1362.5 ms | 17077.8 ms | 20943.0 ms | `agent-fin-v9-10-memory-policy`, `agent-fin-v9-12-price-policy` |
| 客服 HTTP（60） | 1014.1 ms | 15212.5 ms | 60141.6 ms | 本地 Java/RAG/LLM 完整路径；逐 case 延迟见 HTTP 证据 |

P95/P99 样本均少于 100，只作本地长尾定位；不能写成生产容量或 SLO。

### 只读容量曲线（不是生产 SLO）

固定 4 条 `HUMAN_VERIFIED` 只读请求，先 warm-up `4` 次（不进分母），再每个并发档正式 20 次。v2 是优化前基线，v5 是当前代码；两者均为本机完整 HTTP/Java/Worker 链路，答案只保存 hash/长度。

| 并发 | v2 QPS | v5 QPS | v2 LLM 路径 P95 | v5 LLM 路径 P95 | v2/v5 输出 token |
|---:|---:|---:|---:|---:|---:|
| 1 | 0.237 | 0.396 | 14.852 s | 10.211 s | 1317 / 1317 |
| 2 | 0.433 | 0.641 | 15.015 s | 9.852 s | 1192 / 1326 |
| 4 | 0.371 | 0.965 | 19.912 s | 10.574 s | 1646 / 1617 |
| 8 | 0.429 | 1.353 | 18.404 s | 12.013 s | 1459 / 1543 |

v5 warm-up `4/4`，正式执行、状态和安全契约为 `80/80`；c1/c2/c4/c8 QPS 为 `0.396/0.641/0.965/1.353`，LLM 路径 P95 为 `10.211/9.852/10.574/12.013s`。v5 样本比 v4 更大且有 warm-up，但仍受共享本机和外部 Provider 影响，不能宣称严格因果提升。最大完整请求 `12.415s`、最大单次 LLM call `4.736s`；新增 `AGENT_LLM_CALL_DEADLINE_SECONDS=45` 只限制单次生成调用，仍服从 Agent/Worker `120s` 总 deadline。纯社交审计探针 v4 为 `5/5`，P50/P95/P99 `629.6/716.1/726.6 ms`，每条 trace 均有 `deterministicSocialReply=true`，Provider calls/token 为 `0`，usage 为 `NOT_APPLICABLE/no_llm_call`。可选 fast-support 生成实验默认关闭。

## AI 客服核心质量证据

完整逐 case 金标结果见 [客服金标评测](customer-service/客服金标评测.md)，机器证据见 [客服 JSON](customer-service/客服金标评测.json)。下表的
slot 是生产 `resolve_intent(..., allow_llm=False)` 规则预路由口径；标签已完成双人盲标和 lead reviewer 仲裁，状态为
`HUMAN_VERIFIED`，但仍是离线证据且不自动进入 release gate。数据集 60 条，覆盖商品咨询/搜索边界、支付风险、隐私、否定、少件和槽位格式。

| 指标 | 当前值 | 分子/分母 | 95% CI | badcase |
|---|---:|---:|---|---:|
| Intent Macro-F1 | 0.955299 | 19.105978/20 intents | [0.929286, 0.987409] | `011,044,057`：政策/比较边界 |
| 高风险 intent Recall | 1.000000 | 10/10 | [0.722467, 1.000000] | 无 |
| Slot entity/span F1（完整人工 schema） | 0.996364 | 2TP/(2TP+FP+FN)=822/825 | [0.995061, 0.997636] | `009/020/058` |
| 请求级 Slot Exact Match（完整人工 schema） | 0.911765 | 31/34 non-empty-slot cases | [0.770395, 0.969534] | `009/020/058` |
| Handoff Recall | 1.000000 | 14/14 | [0.784689, 1.000000] | 无 |
| 严重漏转人工率 | 0.000000（越低越好） | 0/6 critical | [0.000000, 0.390334] | 无 |

Intent CI 已按 `intent×risk×handoff` 对完整 case 分层并每次重算 Macro-F1；Slot CI 在每次抽样内重新累加 TP/FP/FN 并计算 micro F1。
区间与点估计现在是同一统计量，但 60 条仍不足以推出开放世界泛化性。

关键 badcase 和根因素材：

- `cs-gold-v1-011` / `057`：退款政策/到账时效分别被路由到 `REFUND_STATUS`、`CHAT`，taxonomy 与政策/状态边界不一致。
- `cs-gold-v1-044`：比较型问题被判为 `PRODUCT_SEARCH`，应进入商品咨询/比较路径。
- `cs-gold-v1-001/002/003/029/033/034/041/042/043/045/055/059`：扩展槽位和少件数量已加入确定性抽取，同一人工金标 replay 全部修复。
- `cs-gold-v1-009/020/058`：只剩 `199`、`199元`、`¥199.00` 的严格原始格式差异；业务值可用，但 strict span/EM 仍如实记错。

同一 60 条 gold 的 paired replay：Span F1 `0.907652 -> 0.996364`、EM `0.558824 -> 0.911765`，修复 12 case、回归 0；不可变包为 [customer-service-slot-replay-v1-20260823](../../AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/customer-service/customer-service-slot-replay-v1-20260823/)。这是同集代码变更证据，不是新 holdout。生产 canonical 投影为 Span F1 `0.995683`、EM `0.911765`；当前结果仍不能外推为线上客服成功率、CSAT 或 FCR。

### 客服 HTTP/LLM 全链路

60 条已全部经过正式 `/api/agent/sendMessage` 路径，执行 `60/60`；HTTP 意图 Macro-F1 `0.955299`，handoff 混淆矩阵为
`TP=14, TN=46, FP=0, FN=0`，critical miss `0/6`。Episode 中的原始槽位值会脱敏，因此 HTTP Slot F1/EM 明确为
`UNAVAILABLE`，不把脱敏占位符与 gold 直接比较。

原诊断中 6 条 citation contract 失败是最终 envelope 未复制 `sourceRefs`，对应证据实际存在于 `RAG_RETRIEVAL` trace；离线重建后
结构违规 `0`，且没有重跑 Provider。这只修复“引用是否指向已选证据”，不代表答案语义正确。v2 双人盲审已封存：案件级完全一致
`52/60=0.866667`，answerCorrect 标签一致 `54/60`（κ `0.446154`）、citationSupport `58/60`（κ `0.942857`）、handoff/unsafe 均为
`60/60`。这是标注可靠性证据，不是最终模型质量率；8 条分歧必须由独立第三人仲裁，完成前答案正确率、引用支持率、转人工适当性和
unsafe-answer rate 均为 `PENDING_ADJUDICATION`。冻结包见
[customer-service-answer-review-v2-pending-adjudication-20260824](../../AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/customer-service/customer-service-answer-review-v2-pending-adjudication-20260824/)。

另已新增 60 条 v2 draft（20 个 intent 各 3 条，hard 35，应转人工 16），目标将金标扩到 120 条。在双人盲标、封存和仲裁完成前，它仍是
`DRAFT_NEEDS_DUAL_HUMAN_REVIEW`，不与当前 60 条 HUMAN_VERIFIED 分母合并。

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
客服 HTTP 额外记录 input/output token `114,720/6,649`、Provider call `32`；`31` 次无单价、`1` 次缺 usage，所以总费用仍是 `null`。
容量 v5 正式 input/output token 为 `181,558/5,803`，56 次 Provider call 均有 usage 但无可信账单单价，仍为 `UNPRICED`；按并发档的 output token 分别为 `1,317/1,326/1,617/1,543`，合计 `5,803`。官方目录价估算单独记录为 `ESTIMATED_LIST_PRICE`，不能改写运行时状态。纯社交确定性路径确认没有 LLM 调用，才允许记为 `NOT_APPLICABLE`，而不是把未知费用写成 0。目录价来源与 hash 见 [model-pricing-estimate-20260824.json](model-pricing-estimate-20260824.json)。

## 证据与复现

```bash
conda activate shop
cd AI_Shop-backend/AI_Shop-agent
python -m evaluation.cli validate
python -m evaluation.cli slices --split development
python -m evaluation.cli scorecard --output /tmp/aishop-scorecard.md --json-output /tmp/aishop-scorecard.json
python -m evaluation.cli customer-service-gold --mode rule
python -m evaluation.cli benchmark-capacity --dataset <human-gold.jsonl> --run-id <new-run> --warmup-requests 4 --case-id <read-only-case>
# 客服人工金标已完成；新版本仍必须按同一 fail-closed 流程 seal/compare/merge
python -m evaluation.cli customer-service-review export --annotator reviewer-a --output /tmp/reviewer-a.open.jsonl
```

current 只指向 v9 final；v2 是历史通过 archive，v3-v8 是 immutable failed archive；旧运行不参与当前分母。机器索引、哈希和生命周期见
[evidence-manifest.json](../evidence-manifest.json)。

## 当前不做的外推

没有真实曝光/点击/购买、客服满意度盲评、生产并发、支付合规或长期线上实验，因此不能声称 CTR/CVR/GMV、工业级个性化推荐、生产 SLO、CSAT/FCR
或开放世界客服成功率。后续只做高价值补证：

1. 由独立第三人完成 8 条 HTTP 答案分歧仲裁，再计算答案正确率、引用支持率、转人工适当性和 unsafe-answer rate；在此之前不宣称端到端客服质量。
2. 人工完成 v2 新增 60 条的双人盲标和仲裁，再生成 120 条 HUMAN_VERIFIED v2；新数据上复核分层 CI 和 badcase，不覆盖 v1。
3. Search 优化只针对已固定的 `23/34/47` 多对象召回问题，先做 query decomposition/对象保留的可见集 A/B 成对回放，不修改 qrels 或 v9 final。
4. 当前短容量曲线只用于定位；若需要生产性能结论，需在固定独占环境扩大请求量和持续时间，增加 warm-up、steady-state、stress/soak、外部 Provider 分层和资源瓶颈分析。
