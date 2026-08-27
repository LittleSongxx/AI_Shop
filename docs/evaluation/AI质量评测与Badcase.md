# AI_Shop 质量评测与 Badcase

> 当前客服修复后主质量证据已更新为 v56 的 120 条人工审批评审包；标签与答案均属于 `HUMAN_APPROVED_AI_ASSISTED`，即人工最终决策、AI 辅助文字整理。
> v56 答案正确 `120/120`、引用支持 `67/67`、联合质量 `120/120`、unsafe `0/120`；完整结论见 [`AI-Shop人工审批评测最终结果与Badcase分析-20260827.md`](AI-Shop人工审批评测最终结果与Badcase分析-20260827.md)。
> v54 剩余 7 条已在 v55 定向与 v56 全量运行中完成回归：v56 为 120/120 执行、29/29 契约，A/B 118/120 一致、2 条已人工仲裁，最终 badcase 0。
> v43 保留为修复前基线，v27 及 v30/v31 保留为历史或定向回归；它们都不是独立 unseen holdout。
> 统计环境：Conda `shop`；所有延迟为本地完整链路观测，不是生产 SLO。

## 先看口径

本页只把“有真实输出、非强制全通过、且能回答结果好不好”的证据列为主指标。发布契约、规则预路由回放、模型/标注者一致率、lexical 结构校验和本地容量曲线都保留，但只能作为诊断或安全前置条件。每个主指标都保留分母、95% 区间和 badcase；没有因为 runtime `PASS` 就把坏例抹掉。

## 当前主质量结果

| 域/指标 | 点估计 | 分子/分母 | 95% CI | 质量 badcase/边界 |
|---|---:|---:|---|---|
| Search Recall@10（macro/query，v9） | 0.962121 | 44 query | [0.916667, 1.000000] percentile bootstrap | 3 query / 4 商品；输入已被源码暴露，只能作历史强回归 |
| Search Recall@10（micro/qrel，v9） | 0.928571 | 52/56 | [0.830246, 0.971874] Wilson | 4 漏召回商品 |
| Search MRR@10（v9） | 0.937500 | 44 query | [0.880682, 0.988636] percentile bootstrap | 5 query |
| Search NDCG@10（v9） | 0.920521 | 44 query | [0.868709, 0.964456] percentile bootstrap | 10 query |
| 客服 HTTP v56 答案正确率 | **1.000000** | **120/120** | **[0.968981, 1.000000] Wilson** | 0 条；人工审批、2 条仲裁，非 unseen |
| 客服 HTTP v56 可计分引用语义支持 | **1.000000** | **67/67 eligible** | **[0.945774, 1.000000] Wilson** | 0 条 unsupported |
| 客服 HTTP v56 转人工适当率 | **1.000000** | **120/120** | **[0.968981, 1.000000] Wilson** | 0 条；不代表绝对零风险 |
| 客服 HTTP v56 unsafe answer rate | **0.000000** | **0/120** | **[0.000000, 0.031019] Wilson** | 0 条；上界仍非 0 |
| 客服 HTTP v56 联合质量 | **1.000000** | **120/120** | **[0.968981, 1.000000] Wilson** | 0 条；当前已见客服集 |
| 客服 HTTP v54 联合质量（上一修复基线） | **0.941667** | **113/120** | **[0.884474, 0.971459] Wilson** | 7 条；同一已见集 |
| 客服 HTTP v43 联合质量（修复前基线） | **0.875000** | **105/120** | **[0.803971, 0.922765] Wilson** | 15 条；同一已见集，只作 paired regression 基线 |
| 客服 HTTP v27 答案正确率 | **0.983333** | **59/60** | **[0.911449, 0.997052] Wilson** | `cs-gold-v1-001`；60 条冻结 HTTP 输出、双评 58/60、2 条第三人仲裁 |
| 客服 HTTP v27 可计分引用语义支持 | **0.972222** | **35/36 eligible** | **[0.858303, 0.995080] Wilson** | 同一 `001`；不可判定引用不进入分母，不能把 `sourceRefs` 存在本身当支持 |
| 客服 HTTP v27 联合质量（答案正确且引用支持/不适用） | **0.983333** | **59/60** | **[0.911449, 0.997052] Wilson** | 同一 `001`；冻结 replay、非 release gate |
| DB batch 相对 N+1（100 候选，offer snapshot） | **P50 下降 73.4%** | 1 vs 100 round trip | 不适用（benchmark 对照） | 隔离数据库描述性性能证据，不是线上吞吐/SLO |
| DB batch 相对 N+1（100 候选，decision feature） | **P50 下降 96.6%** | 1 vs 100 round trip | 不适用（benchmark 对照） | 同上；结果等价、rollback probe 通过 |

Search hard-negative v4 的 `0.833333 -> 0.966667` Recall@10、`0.777778 -> 0.944444` micro Recall、`0.725 -> 0.775` MRR、`0.650292 -> 0.759759` NDCG 是同一批已知难例的 paired replay，单独列为定向优化证据，不能写成新 final 或 unseen 泛化。

### 明确排除出主质量表的数字

| 数字类别 | 为什么不当主指标 | 当前用途 |
|---|---|---|
| `Intent Macro-F1=1.0`、Slot F1/EM、规则 handoff Recall | 同一 gold 上 `resolve_intent(..., allow_llm=False)` 的确定性规则回放；不是最终 HTTP 答案，也不是新 holdout | 路由/抽取回归诊断；HTTP Episode slot 仍因脱敏不可测 |
| `50/50`、`25/25`、`pass^8=1.0`、`11/11 behavior contract`、`0 runtime error` | 这些是预先要求全通过的发布或安全契约；全通过只说明没有触发阻断条件 | fail-closed 发布前置条件，不代表语义质量 |
| 双评一致 `58/60`、κ | 评价协议可靠性，不是模型正确率 | 证明人工审查过程可复核；v27 answerCorrect κ 因标签分布不平衡为 `0`，不能美化 |
| RAG lexical `50/50`、retrieval `29/29`、shadow judge | 结构/检索下界或未人工校准的影子评估，不是真实语义真值 | 诊断和安全下界 |
| 本地 QPS/P95/P99、usage/cost | 共享机器、外部 Provider 和未定价环境下的短时观察 | 瓶颈定位；不能写生产 SLO、成本或业务收益 |

### 数值可用性判断

| 证据 | 当前可用程度 | 可以怎么说 | 不能怎么说 |
|---|---|---|---|
| Search 50 条离线 qrel | **可用于简历/面试，但须写历史强回归边界** | `n=50`、44 条有 qrel 的 Recall/MRR/NDCG、95% CI、切片和漏召回/错排 case | 线上 CTR、个性化推荐收益、未见分布泛化或生产 SLO |
| 客服 v56 120 条冻结 HTTP 输出 | **可用于简历/面试，须写人工审批与已见集边界** | A/B 118/120 一致、2 条仲裁；答案 `120/120`、引用 `67/67`、联合 `120/120`，均带 Wilson CI；人工最终决策、AI 辅助编辑 | 线上客服准确率、CSAT/FCR、无人值守能力；不能把案件一致率当模型准确率或把已见集称 unseen |
| DB batch/N+1 | **可用于说明具体工程优化** | 隔离数据库、同一候选集、结果等价，100 候选 P50 降幅和 round-trip 变化 | 生产吞吐、SLO 或端到端收益 |
| RAG/Agent/容量/故障矩阵 | **工程诊断可用** | 明确数据、契约、失败恢复和瓶颈边界 | 人工语义准确率、开放世界 Agent 成功率、生产容量 |

### 当前可用性结论

| 维度 | 判断 | 依据 | 边界 |
|---|---|---|---|
| Search 相关性 | **离线可用，仍有已知难例** | v9 Recall/MRR/NDCG；v4 定向 replay 有实测改善 | v9 输入暴露；`23/34/47` 仍需新 holdout 验证 |
| 客服最终答案 | **v56 当前已见集回归全部通过，但非上线证明** | v56 人工答案 `120/120`，引用 `67/67`，联合 `120/120`；A/B 与仲裁、CI 和零 badcase 结果已封存 | 120 条已被开发使用；零 badcase 不代表绝对安全、未来泛化或 release gate；非 unseen |
| 客服理解/Slot | **只作规则回放诊断** | 同集规则指标；HTTP slot 脱敏不可测 | 不把它写成真实最终答案质量 |
| 性能 | **局部优化有硬证据，端到端生产结论未建立** | DB batch/N+1 实测降幅；本地容量仅诊断 | 需要独占环境 steady-state/stress/soak 才能谈性能目标 |

综合结论：目前最硬的语义证据是 Search 的离线 qrel 排序，以及 v56 的 120 条冻结 HTTP 输出、A/B 人工审批和第三人仲裁；v27/v31 只作为历史阶段保留。最硬的局部性能证据是隔离数据库 batch 对照。这些证据足以支撑严谨的项目经历描述，但 v56 和 Search v4 都使用开发中已见数据，不能包装成线上准确率、unseen 泛化证明或生产 SLO。规则回放与全通过门禁不作为主质量数字。

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

已确认的 Search hard negative：多商品/多品牌 conjunction、否定约束候选不足、比较对象过早收窄。后续 v4 已在固定 qrel 和分母上做定向修复与 paired replay，但没有重刷或改写历史 final。

漏召回的可复核明细：`search-fin-v9-23-snack-100` 漏 `303019597302892`（可口可乐组合）和
`438316828084252`（芒果奶糕），`search-fin-v9-34-snack-no-wangwang` 漏 `303019597302892`，
`search-fin-v9-47-compare-xm` 漏 `350000232815799`（索尼十周年版）。排序 badcase 主要是
`search-fin-v9-11-office`（办公主机相关性靠后）、`28`（预算口红目标排第 4）、`33`（排除户外后仍有无关类目靠前）、
`43/44`（partial provider 下相关商品排序靠后）和 `49/50`（比较型请求混入无关类目或目标次序偏后）；逐商品返回列表仍以 current `bad-cases.jsonl` 为准。

2026-08-23 的 v1 曾对其中 10 条已知难例做真实 Provider 成对回放，baseline/current 的 Recall@10 `0.833333`、micro Recall
`14/18=0.777778`、MRR@10 `0.725`、NDCG@10 `0.650292`，四项 delta 均为 `0`，硬约束违规 `0`。这是修复前历史基线；后续 v4 在同集上得到 Recall@10 `0.966667`、micro Recall `17/18`、MRR `0.775`、NDCG `0.759759`，仍不是新 final 或泛化证据。v1 证据包见
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

## AI 客服规则回放诊断（不列入主质量）

完整逐 case 金标结果见 [客服金标评测](customer-service/客服金标评测.md)，机器证据见 [客服 JSON](customer-service/客服金标评测.json)。下表的
slot 是生产 `resolve_intent(..., allow_llm=False)` 确定性规则预路由回放；即使 gold 已由双人盲标和 lead reviewer 仲裁，它也不等于最终 HTTP
答案质量、开放域泛化或新 holdout。数据集 60 条，覆盖商品咨询/搜索边界、支付风险、隐私、否定、少件和槽位格式；这里只用于发现路由/抽取回归。

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

同一 60 条 gold 的 paired replay：历史规则基线 Intent Macro-F1 `0.955299`（`011/044/057`） -> 当前 `1.000000`，风险不一致 `7 -> 0`；槽位历史 Span F1 `0.907652 -> 0.996364`、EM `0.558824 -> 0.911765`，修复 12 case、回归 0。不可变槽位包为 [customer-service-slot-replay-v1-20260823](../../AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/customer-service/customer-service-slot-replay-v1-20260823/)。这是同集代码变更证据，不是新 holdout，也不进入本页主质量分母。生产 canonical 投影为 Span F1 `0.995683`、EM `0.911765`；当前结果不能外推为线上客服成功率、CSAT 或 FCR。

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

### 客服 HTTP/LLM 全链路：修复后 v13，已完成人工仲裁

动态订单/商品/库存/优惠券/物流/退款等 Java 权威 `sourceRefs` 已由 MCP 工具结果传入 graph state、Episode/step trace、response verifier 和最终 HTTP envelope；政策知识与动态事实仍分开校验。为避免“代码已改但独立 MCP 进程仍在跑旧源码”，API、Worker、MCP 在 readiness 与评测 preflight 中必须报告同一 source fingerprint，否则评测 fail-closed。

该问题有保留的定向复现对照：`customer-service-http-v11-targeted-stale-worker-20260824` 在同一 10 条 HUMAN_VERIFIED 定向集上出现 `6/10` 行为契约违例；完整重启运行时后的 `v12-targeted-after-worker-restart-20260824` 为 `0/10`。两包均为只读、带 `SHA256SUMS` 的 `RUNTIME_VERSION` 排错证据，并由 manifest 强制成对校验相同数据哈希、状态与违例数。它们不进入普通质量分母，不是最终答案正确率或人工引用支持率。

`customer-service-http-v13-20260824` 对冻结 60 条 gold 做了一次真实 Provider 全链路 observation：完整终态 `60/60`、HTTP error `0`、fixture provisioned `19`、cleanup failure `0`、hard-constraint violation `0`、预声明行为契约 `10/10`。它记录了 Intent Macro-F1 `1.0`、高风险 recall `10/10`、handoff recall `14/14`；HTTP slot 仍因脱敏而 `UNAVAILABLE`。这些全是执行/路由/安全契约，**不是**最终回答正确率或引用语义支持率。

usage 为 Provider calls `18`、input/output tokens `78,470/5,486`、`costCny=null`、`costStatus=UNPRICED`；本地 P50/P95/P99 `1015.049/11372.651/22858.230 ms` 标记为 `LOCAL_FULL_STACK_NOT_PRODUCTION_SLO`。原始 Provider observation SHA-256 为 `46916af...e805`，保留在只读 [pre-evaluator-fix 包](../../AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/customer-service/customer-service-http-v13-pre-evaluator-fix-20260824/)；正式 [v13 包](../../AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/customer-service/customer-service-http-v13-20260824/) 的 report SHA-256 为 `2b1b97...94357`，只进行了确定性离线重算，`providerCallsReexecuted=false`。

排错过程中，`cs-gold-v1-001/002/034/042` 的安全免责声明“不能据此断言平台无货”被旧正则只按“平台无货”命中，造成四个 `NO_UNSUPPORTED_CATALOG_ABSENCE_CLAIM` 假阳性。评测器现仅把独立的平台范围缺货断言判错，并保存实际命中的 claim；新增正反两条回归测试。正式 v13 的行为契约违规为 `0`，表示修正了评测器误杀，**不表示业务模型由失败提升为通过**。

v13 原始 report 的 `answerQuality.status` 仍不可变地是 `PENDING_HUMAN_REVIEW`；这描述的是 2026-08-24 生成时的 report，不覆盖已完成的外部人工评审。两份审查表通过来源绑定后封存，案件级完全一致 `49/60=0.816667`，字段级一致为：answerCorrect `59/60`、citationSupport `50/60`（Cohen κ `0.735450`）、handoff `59/60`、unsafe `60/60`。11 条分歧均由独立第三人裁定，最终 evidence 生命周期为 `HUMAN_REVIEWED_ADJUDICATED`，完整包见 [customer-service-http-v13-answer-review-adjudicated-20260824](../../AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/customer-service/customer-service-http-v13-answer-review-adjudicated-20260824/)；双评 pending 包仍作为不可变 parent 保留。

| 指标 | 数值 | 分子/分母 | Wilson 95% CI | badcase |
|---|---:|---:|---|---|
| 答案正确率 | `0.983333` | `59/60` | `[0.911449, 0.997052]` | `012`：待发货状态被过度推断为不能取消，未补查/转人工 |
| 引用语义支持率 | `0.588235` | `20/34` eligible | `[0.422216, 0.736340]` | `004/005/006/007/008/009/012/014/016/017/018/019/035/055` |
| 转人工适当率 | `0.983333` | `59/60` | `[0.911449, 0.997052]` | `012` |
| Unsafe-answer rate（低更好） | `0.000000` | `0/60` | `[0.000000, 0.060172]` | 无；小样本不能称绝对安全 |
| 联合质量通过率 | `0.766667` | `46/60` | `[0.645637, 0.855604]` | 上述 14 条 |

13 条引用 badcase 的可修复根因分为：订单快照没有订单项/商品名/支付场景（`004/005/006/008/014/016/017/035`），回答编入未声明工具能力或未引用的退款/确认后果（`007/009`），以及售后资格规则未随动态订单状态一并引用（`018/019/055`）。`012` 同时暴露取消资格判断和转人工边界。它们是下一轮回归集，不能通过把结构化 `sourceRefs` 存在本身当作 `SUPPORTED` 来消除。v13 与历史 v1 是不同冻结答案与证据传播版本；可描述为后续观察中的不同结果，但不是严格 A/B，也不能把免责声明评测器修复称为模型质量提升。

### 客服 HTTP/LLM 全链路：历史 v20（保留用于回归追溯）

在 v13 后续修复与 v14-v18 诊断基础上，`customer-service-http-v20-20260825` 对同一 60 条 HUMAN_VERIFIED gold 重新执行真实 HTTP Agent 路径。运行完成 `60/60`，行为契约 `11/11`、handoff false positive/negative 均为 `0`、hard constraint violation 和 fixture cleanup failure 均为 `0`；这些机器结果仍不代替答案人工质量。冻结源 report SHA-256 为 `10d171bc...8596d`，源码指纹为 `736cad91...a26`。

两名真人 reviewer 的 sealed 表案件级完全一致 `56/60=0.933333`；字段级一致为 answerCorrect `57/60`、citationSupport `59/60`（Cohen κ `0.973498`）、handoff `60/60`、unsafe `59/60`。4 条分歧 `014/029/043/059` 由独立第三人 `reviewer-c` 裁定。最终只读 evidence 状态为 `HUMAN_REVIEWED_ADJUDICATED`，完整包见 [customer-service-http-v20-answer-review-adjudicated-20260825](../../AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/customer-service/customer-service-http-v20-answer-review-adjudicated-20260825/)；双评 pending 父包继续保留。

| 指标 | 数值 | 分子/分母 | Wilson 95% CI | badcase |
|---|---:|---:|---|---|
| 答案正确率 | `0.950000` | `57/60` | `[0.862995, 0.982850]` | `014/018/043` |
| 引用语义支持率 | `0.694444` | `25/36` eligible | `[0.531437, 0.819955]` | `008/009/014/018/019/020/021/027/029/043/055` |
| 转人工适当率 | `1.000000` | `60/60` | `[0.939828, 1.000000]` | 无；小样本不能称绝对正确 |
| Unsafe-answer rate（低更好） | `0.016667` | `1/60` | `[0.002948, 0.088551]` | `014`：无证据断言确认收货后无法退款，可能误导售后权益判断 |
| 联合质量通过率 | `0.816667` | `49/60` | `[0.700802, 0.894422]` | 上述 11 条 |

v20 的剩余问题是历史回归素材：`008/019/020/021/027/055` 缺创建工单能力、待确认状态或提交后果的可见证据；`009/014` 编入未支持的退款去向、不可撤销性或确认收货后果；`018` 已精确匹配订单却错误声称未定位；`029/043` 的优惠、报价、排序或 Android 硬约束未被可见商品事实逐项支持。`014` 是历史版本的最高风险 badcase，不应再写成当前 v27 结果。

v20 与 v13 的点估计可作为不同冻结输出的描述性观察，但不是严格因果 A/B：答案文本、sourceRefs、Provider 状态及 eligible 引用分母均有变化。不能只报告联合质量 `46/60 -> 49/60` 或引用 `20/34 -> 25/36` 而隐去答案正确 `59/60 -> 57/60` 和 unsafe `0/60 -> 1/60`。源 HTTP report 的生成时字段仍保持 `PENDING_HUMAN_REVIEW`；外部 final package 才是已完成人工生命周期的权威证据。

历史 v2 新增 60 条已经过来源链审计，并在 25 条标签政策重审、5 条仲裁后生成 120 条 v2.1 successor。该集合仍是开发者已见数据，且来源独立复核的 slot exact `0.50` 未达 `0.70` 门槛；因此可作开发回归，不升级为 release/unseen gold。

### 客服 HTTP/LLM 全链路：历史 v27 人工基线

v27 是针对前一轮缺口修复后的同一 60 条 HUMAN_VERIFIED gold 的真实 HTTP Agent 回放。60 条输出由两名独立 reviewer 先盲审，案件级完全一致
`58/60=0.966667`；`2` 条分歧由未参与首轮的 `reviewer-c` 仲裁。最终 immutable package 为
[customer-service-http-v27-full-quality-fixes-answer-review-adjudicated-20260826](../../AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/customer-service/customer-service-http-v27-full-quality-fixes-answer-review-adjudicated-20260826/)，其 pending parent、sealed 原件、仲裁记录、逐 case badcase 和 `SHA256SUMS` 均保留。源 report 的完整文件 SHA-256 为
`f8724dac5c951b30a046dbe30ad3a4ce65b2a60a935aaf42a5720406fb172a61`，但源文件位于被 `.gitignore` 的 `run/`；新 checkout 必须恢复该 provenance 文件才能重跑 manifest 校验。

| 指标 | 数值 | 分子/分母 | Wilson 95% CI | badcase |
|---|---:|---:|---:|---|
| 答案正确率 | `0.983333` | `59/60` | `[0.911449, 0.997052]` | `001` |
| 可计分引用语义支持率 | `0.972222` | `35/36 eligible` | `[0.858303, 0.995080]` | `001` |
| 联合质量（答案正确且引用支持/不适用） | `0.983333` | `59/60` | `[0.911449, 0.997052]` | `001` |
| Unsafe-answer rate（低更好） | `0.000000` | `0/60` | `[0.000000, 0.060172]` | 无；区间上界约 6.02%，不能称绝对安全 |

这四项是人工语义标签，不是规则回放或发布契约；`releaseGateEligible=false`，也不是线上客服准确率、CSAT/FCR 或 unseen 泛化证明。引用分母只包含可判定 claim，`UNDECIDABLE` 不强行计入支持或失败。v27 冻结包中的唯一人工 badcase `cs-gold-v1-001` 的用户指定型号为“索尼 WH-1000XM6”，回答摘要丢失了具体型号，`sourceRefs` 只支持完整型号查询未命中，不能支持扩大的“预算内没有索尼商品”结论。该问题已在后续 v43→v54→v56 修复链中复验关闭；v27 仍作为不可变历史基线，不修改其标签。

v27 的运行报告仍原样保留生成时 `answerQuality.status=PENDING_HUMAN_REVIEW`；这是源报告生命周期，不与外部最终人工包冲突。行为契约 `11/11`、HTTP `60/60`、hard constraint `0` 和 fixture cleanup `0` 仅是安全/执行前置条件，不能替代上表的人工语义结果。

### v30 -> v31：售后资格修复 observation（历史中间运行）

v30 是真实 Provider 的 60-case HTTP observation，源文件为
`AI_Shop-backend/AI_Shop-agent/run/evaluation-observations/customer-service-http-v30-full-quality-fixes-deduped-fixture-20260826.json`，SHA-256 为
`44fa999351fcaeb7db05fc12767a903b7e03ed0b6e843bfa4aa8429b8557afca`。当时 `cs-gold-v1-019` 和 `cs-gold-v1-055` 仍产生 `CREATE_SUPPORT_CASE` proposal，并把订单结果记为 `RESOLVED`，所以 v30 只能作为行为阻断诊断，不能作为质量结果。

修复后的 v31 使用全新 run ID `customer-service-http-v31-return-eligibility-20260826`，没有复用 v30 的 report 或人工标签。源 observation 位于被 `.gitignore` 的本地 run，SHA-256 为
`b7d010374e9a95c52df0287891a4d99342a72cf5ea06e81dcd2b37e1c67f5863`。其 provenance 为：

- 数据集仍是 `HUMAN_VERIFIED` 的 60 条 gold，SHA-256 `112dfd6ba7546b7cbad317597d944e3ab4dc02627d4ca6018733031d8eddc527`；运行 fixture `http-fixtures-v1.json` SHA-256 `fadf095fa921d901fc00315074e52234ae6ac15f011d9e3afe3be90fe6069f6f`。
- catalog snapshot 为 47 个商品，canonical SHA-256 `6b1e3b40b447419d381830f397a5e5dda82ce25d02dd629c49e7f659d57de0cb`；API、Worker、MCP 共享源码指纹 `e0b88975f623f2b56608bbe1cb6960debb8ad60dc89663cba5a1937ddf24ca9f`。
- 真实 Provider/model 配置为 `qwen3.7-plus`、`text-embedding-v4`、`qwen3-rerank`；行为契约文件 SHA-256 `ffb2048cdefc5b5a4b30da1f97f40bc23b48bcd33ca5c45ca2a4dd8249e81706`。

机器执行观察（仅为运行诊断，不是语义质量数字）为：HTTP `60/60`、HTTP error `0`、fixture provisioned `19`、cleanup failure `0`、hard-constraint violation `0`，10 条 v1 behavior contract 全部满足。关键 case 的黑盒结果是：`001` 保留完整型号 `WH-1000XM6`，并在 `sourceRefs.queryScope` 中绑定型号和当前预算查询范围；`019`（商品错发）和 `055`（商品少件）均出现 `NO_ELIGIBLE`，没有 `CREATE_SUPPORT_CASE` proposal。`answerQuality.status=PENDING_HUMAN_REVIEW`、`selfJudged=false`、`releaseGateEligible=false`，这些字段没有被原位改写。

v31 的独立盲审工作区为
`AI_Shop-backend/AI_Shop-agent/run/review-workspaces/customer-service-http-v31-return-eligibility-20260826/`：`reviewer-a.open.jsonl` 与 `reviewer-b.open.jsonl` 各 60 条，随机种子分别为 `781923640512` 和 `294817350691`，标签均为空且通过 `review-validate`。在双人独立审阅、第三人仲裁和新 immutable package 封存之前，v31 不进入主质量表、不更新 v27 的 59/60 或 35/36，也不登记为 `docs/evidence-manifest.json` 的 canonical quality package。

v31 后续已被 120 条 v43/v54/v56 冻结运行覆盖，因此不再作为当前待人审队列。它保留为中间运行证据；当前人工主证据是 v56。

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
| RAG/客服答案 | 检索质量与回答 groundedness/relevance/completeness 分开；LLM judge 只能作为 shadow，人工校准后才可升级为门禁 | lexical/citation 与 semantic shadow 并列；v20 人工引用支持 `25/36=69.44%`，11 条逐 claim badcase 仍需保留，修复后只能用新 run 双盲审 |
| Agent 工具使用 | 交互式评测应记录 tool-call accuracy、终态/状态变化、失败恢复和重复副作用，而非只报 task pass | 已有 `pass^5/pass^8`、state diff、幂等和故障矩阵；不把 `pass=100%` 当客服质量分数 |
| 风险治理 | NIST AI RMF 的 Govern/Map/Measure/Manage 思路对应数据版本、风险边界、可复核证据和 fail-closed 发布 | current/archive、SHA-256、UNVERIFIABLE 与人工仲裁已落地；仍无专家资质或线上用户反馈证据 |

本项目与 InsightVault 的差异应保持清晰：InsightVault 的主证据是文档 RAG 的召回、引用和语义支持；AI_Shop 的主证据是电商硬约束搜索、动态商品/订单权威快照、客服路由、确认写入、幂等和故障恢复，不重复堆一套通用 RAG 排行榜。

资料（访问 `2026-08-24`）：

- BEIR：<https://arxiv.org/abs/2104.08663>；Elastic RRF：<https://www.elastic.co/guide/en/elasticsearch/reference/current/rrf.html>
- Microsoft RAG Evaluators：<https://learn.microsoft.com/en-us/azure/ai-foundry/concepts/evaluation-evaluators/rag-evaluators>；Ragas 指标：<https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/>
- Self-RAG：<https://arxiv.org/abs/2310.11511>；AgentBench：<https://arxiv.org/abs/2308.03688>；ToolBench/ToolLLM：<https://arxiv.org/abs/2307.16789>
- NIST AI RMF：<https://www.nist.gov/itl/ai-risk-management-framework>

后续只按以下顺序投入：

1. 保持 v43/v54/v56 及其 A/B/仲裁证据不可变；后续修改产生新 run、新哈希和新人工包。
2. 由独立保管者生成与源码、历史 badcase 完全隔离的新 unseen holdout，按型号/品牌/否定/比较/金额/动态订单状态分层。
3. 完成 v2 来源独立复核的全 60 条扩展和保管声明；不再重复生产已完成的 v2.1 标签决策。
4. Search v4 已在固定难例上记录查询拆解/候选 union 的 paired 改善；下一步是新 holdout 复验，不改 qrel 或重刷 final。
5. 补充脱敏 HTTP slot typed/hash projection，以及独占环境 steady-state/stress/soak；这些完成前不外推线上指标。

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

1. v56 已完成全量双评与仲裁，当前已见集 badcase 为 0；不再重刷同集制造“提升”。
2. 建立仓库外/源码隔离的 unseen holdout，并在一次性执行后完成客服 claim-level 双评与仲裁。
3. 完成 v2 来源复核全 60 条和保管声明；当前 v2.1 仍是开发诊断数据。
4. 端到端 slot 需要隐私保护的 typed/hash 投影；没有它，不能声称 HTTP slot 质量。
5. Search v4 已有已知难例 paired 改善；新 holdout 泛化验证、独占环境 steady-state/stress/soak 和 Provider 分层仍未完成。
