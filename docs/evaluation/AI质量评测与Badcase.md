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
| AI 客服 HTTP 最终答案（历史 v1） | 正确率 `0.850000`；引用支持 `0.200000` | 60 条双盲+8 条第三人仲裁；引用分母 30 | Wilson | 9 answer、24 citation、28 joint badcase |
| AI 客服 HTTP 最终答案（修复后 v13） | `PENDING_ADJUDICATION` | 60 条真实 HTTP observation；双人已封存，49/60 案件完全一致，11 条待第三人仲裁 | 不适用 | `004/005/006/007/008/009/014/016/017/035` 引用分歧；`012` 答案/转人工分歧 |

Search/RAG/Agent 的运行 summary 也保存了独立固定种子的 bootstrap 区间；由于运行 summary 与 scorecard 是两次独立重采样，NDCG 和 P95 的区间端点可能有小幅差异，点估计、分母和 badcase 必须一致。这里的表格明确标注为 scorecard 区间，不能把两套端点混写成一个“线上置信区间”。

### 数值可用性判断

| 证据 | 当前可用程度 | 可以怎么说 | 不能怎么说 |
|---|---|---|---|
| Search 50 条离线 qrel | **可用于简历/面试** | `n=50`、44 条有 qrel 的 Recall/MRR/NDCG、95% CI、切片和漏召回/错排 case | 线上 CTR、个性化推荐收益或生产 SLO |
| 客服 60 条 HUMAN_VERIFIED + HTTP 答案审查 | **可用于简历/面试，但须完整披露版本边界** | 双人盲标、25 条 lead 仲裁后的 intent/slot/handoff；历史 v1 HTTP 答案的双盲+8 条第三人仲裁；v13 双盲已封存、案件一致率 `49/60=81.67%`，正在第三人仲裁 | 线上客服准确率、CSAT/FCR；不能隐去历史 v1 HTTP 引用支持仅 `6/30=20.0%`、联合质量 `32/60=53.3%`，也不能把 v13 执行 `60/60` 或任一单评结果当作最终答案质量 |
| RAG 50 条、Agent 25 条 | **工程诊断可用** | RAG 检索/引用/拒答下界，Agent 幂等、终态和状态 diff 契约 | 人工语义准确率、开放世界 Agent 成功率 |
| 本地延迟、容量、token、DB、故障矩阵 | **复盘/设计证据可用** | 环境、样本、并发/QPS、P50/P95/P99、usage unknown、batch/N+1 和恢复契约 | 持续生产容量、费用为 0、线上 SLO |

门禁通过（`50/50`、`25/25`、`pass^8`、安全与终态为 100%）只是发布前置条件，不是质量提升分数；质量指标必须和 badcase 一起展示。

### 当前达标与可用性

这里的“达标”指预先声明的项目离线门禁，不是统一行业认证或生产 SLO。

| 维度 | 判断 | 依据 | 对系统可用性的含义 |
|---|---|---|---|
| Search 相关性与约束 | **达标** | Recall@10 `0.9621`、MRR `0.9375`、NDCG `0.9205`，硬约束违规 `0` | 足以支撑当前 47 商品、小流量场景的可用搜索/导购；多对象和比较型难例仍需 fallback/澄清 |
| RAG 受控问答 | **有条件达标** | answerable Recall@5 `29/29`，lexical grounded/citation/no-answer 门禁通过 | 足以支撑封闭知识库 FAQ；没有独立人工语义真值，不能证明开放域答案准确率 |
| 客服理解与转人工 | **当前离线点估计达标** | 同一 60 条 HUMAN_VERIFIED gold 重跑后 Intent Macro-F1 `1.0000`、高风险和 handoff Recall `1.0`；raw full-slot F1 `0.9964`、normalized F1/EM `1.0` | 可用于带澄清和人工兜底的客服路由；这是同集规则回放，不是开放世界泛化，写操作仍须 Java 校验和确认 |
| 客服 HTTP 最终答案与引用 | **历史 v1 未达；修复后 v13 待仲裁** | v1 人工仲裁后答案正确 `51/60=85.0%`，引用语义支持 `6/30=20.0%`，联合质量 `32/60=53.3%`；v13 双人已完成且 11 条分歧已导出，最终质量率仍待第三人仲裁 | 可保留路由、确认和人工兜底；在 v13 仲裁完成前，不能将新生成回答包装为可独立交付的 grounded 客服 |
| Agent 交易可靠性 | **冻结场景达标** | 25 case、200 trials，`pass^8=1.0`，state diff/终态全匹配，重复副作用 `0`；故障 contract `11/11 HARD + 1/1 SHADOW` | 支撑受控交易提案、确认和幂等执行；不等于开放世界 Agent 成功率 |
| 单请求交互延迟 | **Search 可用，生成链路长尾偏高** | 本地 Search P95 `0.80s`、RAG `4.25s`、Agent `17.08s`、客服 HTTP `15.21s`，客服 P99 `60.14s` | 搜索体验可用；Agent/客服需要流式反馈、超时降级和长尾优化，不能承诺生产 SLO |
| 容量与成本 | **已有扩大后的本地诊断，生产结论未建立** | 只读 4 case、warm-up `4`、正式 `80` 请求（并发 `1/2/4/8`、每档 20）；c8 `1.353 QPS`；生成路径 P95 `10.211–12.013s`；usage 有 token 但未定价 | 可定位并发瓶颈和回归，不能据此承诺持续吞吐、生产 SLO 或单位经济性 |

综合结论：当前证据足以支撑小商品集、低并发、人工最终兜底下的检索、路由和受控交易演示，也足以作为求职项目展示；尚不足以证明无人值守、高并发生产系统。历史 HTTP v1 答案仲裁暴露出引用支持 `20.0%` 和联合质量 `53.3%` 的关键缺口。v13 已针对证据传播、路由与验证器误报完成真实回放，双人审查的案件级一致率为 `81.67%`，但 11 条分歧尚未由第三人裁定，因此生成式客服回答仍不能独立上线。其余限制是 Agent/客服生成链路长尾、共享本机负载仍非生产容量、真实账单价格未知，以及客服金标仍只有 60 条。

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
| Intent Macro-F1 | 1.000000 | 20/20 intents | [1.000000, 1.000000] | 无（同集优化回放；边界修复见下） |
| 高风险 intent Recall | 1.000000 | 10/10 | [0.722467, 1.000000] | 无 |
| Slot entity/span F1（完整人工 schema） | 0.996364 | 2TP/(2TP+FP+FN)=822/825 | [0.995061, 0.997636] | `009/020/058` |
| 请求级 Slot Exact Match（完整人工 schema） | 0.911765 | 31/34 non-empty-slot cases | [0.770395, 0.969534] | `009/020/058` |
| Handoff Recall | 1.000000 | 14/14 | [0.784689, 1.000000] | 无 |
| 严重漏转人工率 | 0.000000（越低越好） | 0/6 critical | [0.000000, 0.390334] | 无 |

Intent CI 已按 `intent×risk×handoff` 对完整 case 分层并每次重算 Macro-F1；Slot CI 在每次抽样内重新累加 TP/FP/FN 并计算 micro F1。
当前 1.0 是在冻结的同一 60 条人工 gold 上的规则回放，不是新 holdout；不能用它推断开放世界泛化性。raw slot 指标保留金额格式差异，normalized 指标仅作业务归一化诊断。

关键 badcase 和根因素材：

- `cs-gold-v1-011` / `057`：旧规则把无订单的退款政策/到账时效分别路由到 `REFUND_STATUS`、`CHAT`；现改为无具体退款上下文走 `REFUND` 知识回答，带订单/已发起退款仍走 `REFUND_STATUS`。
- `cs-gold-v1-044`：旧规则把“这款耳机和另一款相比哪个好”当成 `PRODUCT_SEARCH`；现保留同类比较对象并进入 `PRODUCT_CONSULT` 澄清，跨品牌/跨品类比较仍走搜索。
- 风险字段：旧规则将退款/取消/确认收货/改地址/售后提案默认为 LOW；现按状态变更风险标为 MEDIUM，发票/评价仍按既有低风险契约。
- `cs-gold-v1-001/002/003/029/033/034/041/042/043/045/055/059`：扩展槽位和少件数量已加入确定性抽取，同一人工金标 replay 全部修复。
- `cs-gold-v1-009/020/058`：只剩 `199`、`199元`、`¥199.00` 的严格原始格式差异；业务值可用，但 strict span/EM 仍如实记错。

同一 60 条 gold 的 paired replay：历史规则基线 Intent Macro-F1 `0.955299`（`011/044/057`） -> 当前 `1.000000`，风险不一致 `7 -> 0`；槽位历史 Span F1 `0.907652 -> 0.996364`、EM `0.558824 -> 0.911765`，修复 12 case、回归 0。不可变槽位包为 [customer-service-slot-replay-v1-20260823](../../AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/customer-service/customer-service-slot-replay-v1-20260823/)。这是同集代码变更证据，不是新 holdout。生产 canonical 投影为 Span F1 `0.995683`、EM `0.911765`；当前结果仍不能外推为线上客服成功率、CSAT 或 FCR。

### 客服 HTTP/LLM 全链路：历史 v1 人工基线

60 条已全部经过正式 `/api/agent/sendMessage` 路径，执行 `60/60`；HTTP 意图 Macro-F1 `0.955299`，handoff 混淆矩阵为
`TP=14, TN=46, FP=0, FN=0`，critical miss `0/6`。Episode 中的原始槽位值会脱敏，因此 HTTP Slot F1/EM 明确为
`UNAVAILABLE`，不把脱敏占位符与 gold 直接比较。

原诊断中 6 条 citation contract 失败是最终 envelope 未复制 `sourceRefs`，对应证据实际存在于 `RAG_RETRIEVAL` trace；离线重建后
结构违规 `0`，且没有重跑 Provider。这只修复“引用是否指向已选证据”，不代表答案语义正确。随后两位标注者完成 60 条盲审：案件级四标签
完全一致 `52/60=0.866667`，answerCorrect 一致 `54/60`（κ `0.446154`）、citationSupport 一致 `58/60`（κ `0.942857`）、handoff/unsafe
均为 `60/60`。这是标注可靠性，不是模型质量率；`8` 条分歧由独立第三人逐条仲裁后，最终 HTTP 回放质量为：

客服理解金标的标注审计另行记录人工标签可靠性：全字段案件级一致 `35/60`，但 intent 字段一致 `57/60`（κ `0.945913`）、risk `56/60`
（κ `0.889908`）、slots `45/60`（κ `0.662035`）。因此不能把 `35/60` 简写成“意图准确率 58.3%”；它主要反映槽位命名/金额格式
分歧。HTTP 引用审计中 `UNVERIFIABLE_RUNTIME_FACT` 共 `24` 条，其中 `19` 条人工仍判答案逻辑正确，二者都不应改写为模型错误率。

| 指标 | 数值 | 分子/分母 | Wilson 95% CI | 主要 badcase |
|---|---:|---:|---|---|
| 答案正确率 | 0.850000 | 51/60 | [0.738854, 0.919026] | `003/012/027/029/033/041/044/045/059`：对象缺失时澄清错误、取消流程未处理、属性问答退化为搜索 |
| 引用语义支持率 | 0.200000 | 6/30 eligible | [0.095051, 0.373057] | 24 条：动态订单/券/商品事实没有可核对 `sourceRefs`，或通用政策不能支持具体动作/状态 |
| 转人工适当率 | 1.000000 | 60/60 | [0.939828, 1.000000] | 无；小样本不能声称绝对正确 |
| Unsafe-answer rate（低更好） | 0.000000 | 0/60 | [0.000000, 0.060172] | 无；区间上界仍为 6.0% |
| 联合质量通过率 | 0.533333 | 32/60 | [0.408934, 0.653721] | 28 条，主要由引用不支持驱动 |

完整只读包见 [customer-service-answer-review-v2-adjudicated-20260824](../../AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/customer-service/customer-service-answer-review-v2-adjudicated-20260824/)。它包含 sealed 原件、第三人仲裁、最终 labels、逐 case badcase、`SHA256SUMS`，并由项目 manifest 复核；`releaseGateEligible=false`，不对历史 v9 final 追溯设门禁。

### 客服 HTTP/LLM 全链路：修复后 v13，双盲已封存、待第三人仲裁

动态订单/商品/库存/优惠券/物流/退款等 Java 权威 `sourceRefs` 已由 MCP 工具结果传入 graph state、Episode/step trace、response verifier 和最终 HTTP envelope；政策知识与动态事实仍分开校验。为避免“代码已改但独立 MCP 进程仍在跑旧源码”，API、Worker、MCP 在 readiness 与评测 preflight 中必须报告同一 source fingerprint，否则评测 fail-closed。

该问题有保留的定向复现对照：`customer-service-http-v11-targeted-stale-worker-20260824` 在同一 10 条 HUMAN_VERIFIED 定向集上出现 `6/10` 行为契约违例；完整重启运行时后的 `v12-targeted-after-worker-restart-20260824` 为 `0/10`。两包均为只读、带 `SHA256SUMS` 的 `RUNTIME_VERSION` 排错证据，并由 manifest 强制成对校验相同数据哈希、状态与违例数。它们不进入普通质量分母，不是最终答案正确率或人工引用支持率。

`customer-service-http-v13-20260824` 对冻结 60 条 gold 做了一次真实 Provider 全链路 observation：完整终态 `60/60`、HTTP error `0`、fixture provisioned `19`、cleanup failure `0`、hard-constraint violation `0`、预声明行为契约 `10/10`。它记录了 Intent Macro-F1 `1.0`、高风险 recall `10/10`、handoff recall `14/14`；HTTP slot 仍因脱敏而 `UNAVAILABLE`。这些全是执行/路由/安全契约，**不是**最终回答正确率或引用语义支持率。

usage 为 Provider calls `18`、input/output tokens `78,470/5,486`、`costCny=null`、`costStatus=UNPRICED`；本地 P50/P95/P99 `1015.049/11372.651/22858.230 ms` 标记为 `LOCAL_FULL_STACK_NOT_PRODUCTION_SLO`。原始 Provider observation SHA-256 为 `46916af...e805`，保留在只读 [pre-evaluator-fix 包](../../AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/customer-service/customer-service-http-v13-pre-evaluator-fix-20260824/)；正式 [v13 包](../../AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/customer-service/customer-service-http-v13-20260824/) 的 report SHA-256 为 `2b1b97...94357`，只进行了确定性离线重算，`providerCallsReexecuted=false`。

排错过程中，`cs-gold-v1-001/002/034/042` 的安全免责声明“不能据此断言平台无货”被旧正则只按“平台无货”命中，造成四个 `NO_UNSUPPORTED_CATALOG_ABSENCE_CLAIM` 假阳性。评测器现仅把独立的平台范围缺货断言判错，并保存实际命中的 claim；新增正反两条回归测试。正式 v13 的行为契约违规为 `0`，表示修正了评测器误杀，**不表示业务模型由失败提升为通过**。

v13 原始 report 的 `answerQuality.status` 仍不可变地是 `PENDING_HUMAN_REVIEW`；当前生命周期已推进到双人封存后的 `PENDING_ADJUDICATION`。两份审查表已通过来源绑定校验并封存，案件级完全一致 `49/60=0.816667`，字段级一致为：answerCorrect `59/60`、citationSupport `50/60`（Cohen κ `0.735450`）、handoff `59/60`、unsafe `60/60`。11 条待仲裁为 `004/005/006/007/008/009/014/016/017/035/012`；其中前 10 条仅引用支持分歧，`012` 同时有答案正确和转人工分歧。待仲裁只读包见 [customer-service-http-v13-answer-review-pending-adjudication-20260824](../../AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/customer-service/customer-service-http-v13-answer-review-pending-adjudication-20260824/)，第三人填写文件为项目根目录 `adjudication.answer-review-v13.open.jsonl`。必须完成第三人裁定后，才能计算 v13 的答案正确率、引用语义支持率、转人工适当率、unsafe-answer rate、联合质量和新的 badcase；历史 v1 的 `85.0%/20.0%/53.3%` 不得迁移或对比成提升。

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

## 方法调研与当前优化优先级（2026-08-24）

外部资料支持的成熟做法不是“让 LLM 直接给答案/商品”，而是把可验证事实和生成解释分层：

| 问题 | 业界/论文做法 | AI_Shop 当前落地与差距 |
|---|---|---|
| 客服理解 | 任务型对话通常分别评估 intent、slot、dialogue state/action；分类同时看 Macro-F1、关键类 Recall 和混淆 badcase | 已有 60 条双人盲标+仲裁；slot 另报 raw/normalized，风险与转人工独立。当前缺的是更大、领域经验标注的 holdout |
| 商品检索 | BM25 是稳健 lexical baseline；向量用于同义泛化，RRF 融合多路排序，再做 rerank；硬型号/数字/品牌/否定约束必须在召回后强过滤 | 当前 Search 已有 Recall/MRR/NDCG、硬约束门禁；下一步只处理 `23/34/47` 的多对象/比较 query decomposition，不改 qrel 刷分 |
| RAG/客服答案 | 检索质量与回答 groundedness/relevance/completeness 分开；LLM judge 只能作为 shadow，人工校准后才可升级为门禁 | lexical/citation 与 semantic shadow 并列；旧 HTTP 引用支持 `6/30=20.0%`，必须先补动态事实 sourceRef，再新 run 双盲审 |
| Agent 工具使用 | 交互式评测应记录 tool-call accuracy、终态/状态变化、失败恢复和重复副作用，而非只报 task pass | 已有 `pass^5/pass^8`、state diff、幂等和故障矩阵；不把 `pass=100%` 当客服质量分数 |
| 风险治理 | NIST AI RMF 的 Govern/Map/Measure/Manage 思路对应数据版本、风险边界、可复核证据和 fail-closed 发布 | current/archive、SHA-256、UNVERIFIABLE 与人工仲裁已落地；仍无专家资质或线上用户反馈证据 |

本项目与 InsightVault 的差异应保持清晰：InsightVault 的主证据是文档 RAG 的召回、引用和语义支持；AI_Shop 的主证据是电商硬约束搜索、动态商品/订单权威快照、客服路由、确认写入、幂等和故障恢复，不重复堆一套通用 RAG 排行榜。

资料（访问 `2026-08-24`）：

- BEIR：<https://arxiv.org/abs/2104.08663>；Elastic RRF：<https://www.elastic.co/guide/en/elasticsearch/reference/current/rrf.html>
- Microsoft RAG Evaluators：<https://learn.microsoft.com/en-us/azure/ai-foundry/concepts/evaluation-evaluators/rag-evaluators>；Ragas 指标：<https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/>
- Self-RAG：<https://arxiv.org/abs/2310.11511>；AgentBench：<https://arxiv.org/abs/2308.03688>；ToolBench/ToolLLM：<https://arxiv.org/abs/2307.16789>
- NIST AI RMF：<https://www.nist.gov/itl/ai-risk-management-framework>

后续只按以下顺序投入：

1. 用新预注册 holdout 验证 sourceRef 传播修复，重新做客服答案 correctness/grounding 双盲+仲裁；旧 `20%` 结果保持不可变。
2. 完成 v2 另外 60 条的双人盲标，重点校准退款政策/状态、支付方式、比较和风险边界，再报分层 CI 与 badcase。
3. 在固定 Search 难例上做查询拆解、候选集合保留和二阶段排序的 paired A/B；只有同一 qrel、同一分母、无约束回归才记录质量提升。
4. 暂不投入 CTR/CVR/GMV、开放域客服成功率或生产 SLO；当前样本和环境不支持这些结论。

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

1. 优先修复 24 条 citation badcase：动态订单/优惠券/库存等权威事实必须携带可核对来源，通用政策不得替代具体状态；在新的预注册 holdout 上重新双盲审，不能改写本包或只在同集报提升。
2. 人工完成 v2 新增 60 条的双人盲标和仲裁，再生成 120 条 HUMAN_VERIFIED v2；新数据上复核分层 CI 和 badcase，不覆盖 v1。
3. Search 优化只针对已固定的 `23/34/47` 多对象召回问题，先做 query decomposition/对象保留的可见集 A/B 成对回放，不修改 qrels 或 v9 final。
4. 当前短容量曲线只用于定位；若需要生产性能结论，需在固定独占环境扩大请求量和持续时间，增加 warm-up、steady-state、stress/soak、外部 Provider 分层和资源瓶颈分析。
