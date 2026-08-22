# AI_Shop AI 应用求职项目证据总览

> 当前发布：`release-20260822-ai-quality-v9` / `final-20260822-ai-quality-v9`
>
> 最后核验：2026-08-22（Asia/Shanghai）；Python 运行环境为 Conda `shop`

本页只陈述源码、运行记录和不可变证据包能够复核的事实。机器入口是
[evidence-manifest.json](evidence-manifest.json)；它校验 suite、数据锁、运行文件集、SHA-256、生命周期和归档只读状态。

本轮优化、排错过程、阶段指标和新版面试报告复核见 [AI质量闭环工作记录](AI质量闭环工作记录_20260822.md)；方法成熟度和外部资料对照见
[AI主线方法成熟度与后续优化路线](AI主线方法成熟度与后续优化路线_20260820.md)。

## 项目闭环

AI_Shop 的 AI 主线是“商品 Search + 可信 RAG + 受控业务 Agent”。Python 负责自然语言理解、检索、解释和编排，Java
微服务负责商品、SKU、库存、报价、订单、支付和售后等权威事实及写入边界。

| 域 | 用户路径 | 可复核终点 |
|---|---|---|
| Search | 自然语言需求 -> BM25/向量召回 -> RRF/rerank -> Java 商品/库存/报价快照 -> 硬约束 -> 商品列表 | 商品 ID 排名、qrel、预算/品牌/排除约束、provider trace |
| RAG | 问题 -> 混合检索/重排 -> 版本与注入隔离 -> 最小证据 -> 带引用回答或拒答 | canonical fact/source、claim、引用、拒答和注入判定 |
| Agent | HTTP/队列/LangGraph -> RAG 或工具 -> 用户确认 -> Java 权威接口 -> 终态校验 | Episode/step、工具参数、before/after 状态、幂等和终态 |

因此可以清晰描述闭环：先发现商品或政策事实，再形成只读回答或业务提案；写操作必须经过确认、Java 身份/状态校验和
幂等执行，最终以权威数据库状态而不是模型文案判断成功。远程结果未知只能进入 `INCONCLUSIVE` 或 `MANUAL_REVIEW`。

## 评测协议与数据分层

评测入口只有 `AI_Shop-backend/AI_Shop-agent/evaluation/`。所有命令都复用真实 Provider preflight、运行环境、脱敏、哈希和
fail-closed 规则；执行 Python 时使用：

```bash
conda activate shop
python -m evaluation.cli validate
python -m evaluation.cli verify
```

数据分为 development（可见调优）、regression（可见防回归）、final holdout（一次性未见数据）和独立 benchmark/fault
证据。development 与 regression 的 case ID 以及 `{domain,input}` 指纹互斥；final holdout 不加入 Git。

| Split | Search | RAG | Agent | 总数 | canonical SHA-256 |
|---|---:|---:|---:|---:|---|
| development | 18 | 18 | 7 | 43 | `01cbf2996f9d0ba7b47503dc748b6dcc18374d7cdcb7404c5df22006b67ffa50` |
| regression | 20 | 26 | 5 | 51 | `a102ed7a7e6225ad52da9a60c05c25f8c9f22a2ed5f036395b00c906e802eeb2` |
| final holdout | 50 | 50 | 25 | 125 | `d02e89e644fc115e55c5553baeb98681178822d3cc2e7395003b7cfc3bb5cd07` |

Final 分层固定为 Search `10/10/8/8/6/4/4`（精确型号数字品牌、中文同义口语、预算结构化、否定排除、无结果冲突、
fallback/partial provider、类目品牌比较）；RAG `25/8/8/5/4`（answerable、no-answer、injection、temporal/contradiction、
terminology/citation）；Agent `8/7/4/6`（shopping、RAG/政策、handoff/safety、confirmation/idempotency/write）。

## 当前 final 结果

| 域 | 结果 | 关键点估计 | 硬门禁 |
|---|---:|---|---|
| Search | 50/50 | Recall@3 `0.928030`、Recall@5/10 `0.962121`、MRR@10 `0.937500`、NDCG@10 `0.920521` | case/slice `1.0`、约束违规 `0`、provider completeness `1.0` |
| RAG | 50/50 | retrieval、generation、required claim、citation、grounded faithfulness、no-answer、injection 均通过 | invalid citation、严重安全违规、runtime error 均 `0` |
| Agent | 25/25；200/200 trials | `pass^8=1.0`、critical workflow pass power `1.0`、terminal/state diff `1.0` | 重复副作用、runtime error、严重安全违规均 `0` |

Search 每个正常 slice 独立报告 Recall/MRR/NDCG、no-result、hard constraint、provider completeness、casePassRate、P50/P95/P99、
fallback/partial/deadline 和拒绝原因；不计算加权总分掩盖单 slice 失败。变形检查覆盖预算单调性、排除品牌、无结果不放宽、
精确型号不被宽泛变体覆盖、partial provider 不引入不存在商品。

## RAG semantic shadow judge

lexical claim、事实 ID、引用支持和 grounded faithfulness 是硬门禁下界，永不被语义 judge 覆盖。v9 额外保存 judge 版本、prompt
hash、model/provider fingerprint、timeout/重试、claim ID、answer span、evidence fact/source ID、label、confidence 和
abstain reason。Final `50/50` 有可追溯 semantic shadow 记录、disagreement `0`；它不等于人工真值、人工准确率或人工一致性，
在独立人工校准完成前永远不阻断发布。judge 调用失败必须记录 `UNAVAILABLE`，不能写成 0 分或 1 分。

## Agent 重复与状态证据

development/regression 的 visible repeat 使用 `k=5`；final 使用 `k=8`。每个 trial 有独立 `trialId`、evaluation user、request ID、
幂等键和隔离状态，并保存 API envelope、Episode/step、工具及字段参数、Java authoritative before/after hash、结构化 state diff、
重复副作用、token、延迟、重试和错误分类。只读 case 要求空 diff，提案未确认不得写入，确认写操作只允许预声明变化。

v9 final 的 25 个 case 共 200 trials，`pass^8=1.0`；6 个关键写/确认流程全部通过，重复扣库存、建单或退款为 `0`。这只是
冻结数据上的可靠性证据，不是开放世界成功率或生产容量证明。

## 故障恢复与 benchmark

`fault-v9-20260822` 覆盖 BM25/vector/embedding/rerank、Java 商品/库存/报价、LLM malformed、Redis checkpoint、worker deadline、
MCP 失败和重复请求。每个 scenario 预声明 fallback、unsafe answer、hard-constraint bypass 和 terminal state；判定同时检查
failure trace、降级标识、deadline、硬约束、RAG 拒答/降级、Agent 幂等以及故障恢复后的下一请求。

`db-benchmark-v9-20260822` 使用隔离真实 MySQL 8.4.11，候选规模为 `1/10/50/100`，比较 batch 与 N+1 offer snapshot/decision feature，
并记录 round trips、连接使用、P50/P95、错误率和 rollback probe。100 候选时 batch 为 1 次 round trip，N+1 为 100 次；所有错误率为 0，
回滚探针通过。这个 benchmark 是本地描述性证据，不是线上 SLO、容量或并发结论。

## Usage、费用和置信区间

统一 usage 结构区分 `PRICED`、`UNPRICED`、`MISSING_USAGE`。Provider 未返回 usage 时保留缺失状态；没有可信单价时
`costCny=null`，绝不写成零。v9 final 的 RAG usage 有 token 记录但单价未知，Search/部分 Agent deterministic path 有缺 usage，
因此没有启用费用预算硬门禁。二项指标使用 Wilson 区间，连续/ranking 指标使用 percentile bootstrap；P99 在样本少于 100 时标为
描述性统计。所有延迟边界是本地完整链路观测，不是生产 SLO。

## 证据生命周期

- current：`evaluation-evidence/current/`，只指向已发布的 v9 final；SHA256SUMS digest 为
  `94edeb894f3e2d36f597cb9cc9796d6e0e6f6b81da08f030b8723435e441c11a`。
- archive：v2 是历史通过 final；v3-v8 是 immutable failed final archives，均保留原始文件和原始 SHA256SUMS，文件不可写。
- visible：`.runs/` 只保留 v9 development、v9 regression、v9 final 三个主线目录；旧运行的可追溯性来自 archive/lifecycle/manifest。
- holdout：final holdout 位于 ignored `.holdouts/`，不加入 Git，不能用同一 hash 重跑或改写分母。

## 求职表达边界

可以描述：混合检索、硬约束商品搜索、可信 RAG、MCP/工具编排、确认后写入、Java 权威状态、幂等、故障恢复和可复核评测。
不能描述：已经获得 CTR/CVR/GMV、工业级个性化推荐、生产容量/SLO、支付合规或人工语义准确率。47 商品目录和 125 条 final
样本足以证明闭环工程和质量门禁，不足以证明真实用户收益、长期稳定性或大规模线上效果。
