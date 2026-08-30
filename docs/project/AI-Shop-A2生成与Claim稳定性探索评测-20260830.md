# AI-Shop A2 生成与 Claim 稳定性探索评测

> 日期：2026-08-30（Asia/Shanghai）
> 代码提交：`bacbe5e63d69c218d01be99fc9e638e37527b47b`
> 产品范围：AI 购物导购、AI 客服 Agent
> Text2SQL：`FROZEN`，本批未开发、未运行 unseen、未修改历史证据

```text
EXPLORATORY_EXTERNAL_MODEL_HOLDOUT: TRUE
FINAL_UNSEEN: FALSE
RELEASE_GATE_ELIGIBLE: FALSE
ROLE_SEPARATION: BLOCKED_ROLE_SEPARATION
POST_HOC_REUSED_CANDIDATE: TRUE
NOT_PRODUCTION_SLO: TRUE
TEXT2SQL_STATUS: FROZEN
```

## 1. 实现结论

A2 在共享 response verifier 根路径完成 fail-closed 收口：

- RAG 显式事实与发布版 canonical claim 按 assertion clause 保留，引用精确绑定各自 ref；
  repair 失败只允许使用可自校验的确定性 fallback，否则终态标为 generation unverified。
- 订单、物流、退款、库存、价格、优惠券、评价和工单的动态断言必须绑定对应 Java-owned、
  `matched != false`、非显式 `authoritative=false` 的 business ref；工具名称只代表调用尝试。
- 订单状态、金额、订单项数量、支付方式、商品名/规格，以及各动态 ref 的状态、库存、报价、
  到期时间等按 clause、对象、字段和值核对；多对象、冲突快照、相反后句和连接续写均拒绝。
- cancel/receipt/review/recomment 与 refund/return capability 遍历全部 assertion，绑定当前
  `orderId/orderItemId`、action、decision 和极性；generic policy 只能由发布版 RAG 支持，
  不能借 bounded Java decision 或合法 RAG 引用支持具体订单资格。
- 失败工具不进入 `tools_called`，不传播 business refs、biz payload 或 cards；同 ID 的不同
  business ref 类型不会被 RAG 去重规则吞并。
- multi-agent 继续执行局部引用检查、全局 ref 重映射、draft/fact claim-set 相等和写提案
  fail-closed；finalize 后 memory 只接收真正赢得持久化终态的文本与 cards。

没有新增依赖、数据库表、阈值、qrel、Text2SQL 代码或 formal unseen 配置。

## 2. 代码验证

- handoff 指定 8 个定向文件：`306 passed`。
- Python 全量（排除两个既有 private-holdout 基线文件）：
  `1867 passed, 9 skipped, 1 warning`。
- 两个历史基线文件单独执行：`7 failed, 5 passed`；7 个失败仍全部只因缺少
  `evaluation/.holdouts/final-holdout-20260822-ai-quality-v9.jsonl`，未补空文件或伪造通过。
- 17 个 A2 文件 Ruff 与 `git diff --check`：通过。
- 最终独立审查：共享 verifier SHA-256
  `4b1d38204f36e5a3efabb2d904af6de6f8b7475bff222b643101985d17cca68d`，
  `P0=0`、`P1=0`；77 个 focused check 与 168 个独立对抗探针未发现 fail-open。
- v4 observation 离线 post-hoc：RAG 50 条、`SUPPORTED=28`、显式事实 28、
  deterministic fallback `28/28`、self-verified `28/28`，历史显式生成失败检出 `18/18`。
- catalog canonical claim round-trip：`75/75`。

最后两项均未重新调用 Provider，不是 v5、formal unseen 或 release-gate 证据。

## 3. v5 探索性真实外部模型运行

### 3.1 生命周期与指纹

- release：`exploratory-20260830-mainlines-v5`
- run：`exploratory-external-20260830-v5`
- source commit：`bacbe5e63d69c218d01be99fc9e638e37527b47b`
- candidate raw SHA-256：
  `9769c8e03ffd7a31c48b939c868fda1f1f730f6f47d2f14cbcbfea0623d0168d`
- candidate canonical SHA-256：
  `df1e31fde1233b0b5b07d0f45d918572dbdee2812f4ab1f81357ae00e8f94c90`
- source SHA-256：
  `900528b5cabd0e686659eb015d05d018f8f57533f5e6d12112ada4e8657e4a76`
- knowledge SHA-256：
  `95e19ba2243e33901edcb56be821d85ef8cd4f24e767360a4f5bc17c891e275d`
- provider configuration SHA-256：
  `e1219f9790d95fd89dda864ff4d2fcf928b2d29808d3f049d380de7d103fa24e`
- 模型：`qwen3.7-plus` / `text-embedding-v4` / `qwen3-rerank`
- 执行时间：`2026-08-30T09:03:12.644Z` 至 `2026-08-30T09:17:15.798Z`
- 退出码：`2`；lifecycle：`EXECUTED`；outcome：`FAILED`

运行前 `validate` 通过；带显式 `AI_EVAL_ENABLE_WRITE_FIXTURES=true` 的 regression preflight
通过 Java、Agent API/Worker/MCP 源指纹、MySQL、Redis、Elasticsearch、商品目录、真实
embedding/rerank/LLM 和本地写 fixture 边界。第一次本地启动在 lifecycle 前因共享 Nacos
凭据不一致 fail-closed；同步隔离环境已持有的本地密码后重启并完整通过，未产生 v5 candidate
或 runner 半成品。

### 3.2 v4 / v5 聚合对照

v4 与 v5 使用同一 repaired-v4 candidate，因此只是 post-hoc paired diagnostic；Provider
仍有随机性，不能写成严格因果 A/B。

| 域 | v4 | v5 | 安全解释 |
| --- | ---: | ---: | --- |
| Search case pass | `44/50` | `44/50` | 六个固定 relevance contract 失败不变 |
| RAG case pass | `31/50` | `49/50` | A2 canonical fallback/claim contract 使 18 条历史生成失败收口；仍有 1 条失败 |
| Agent case pass | `19/25` | `21/25` | 4 条仍失败；不能概括为 Agent 全面通过 |
| 总门 | `FAILED` | `FAILED` | Search、RAG、Agent 三域门均未全部通过 |

Search 排名指标未变化：Recall@10 `0.863636`、MRR@10 `0.782955`、
NDCG@10 `0.788914`、NDCG@5 `0.774446`、no-result accuracy `0.94`，
硬约束违规 `0`。A2 没有调整 Search qrel、阈值或排序策略。

RAG retrieval Recall@5 `1.0`、MRR@10 `0.956897`、NDCG@5 `0.858217`，
source coverage/precision 均为 `0.965517`，与 v4 相同；generation correctness、required
claim completeness、citation support 从 `0.62` 升至 `0.98`，grounded faithfulness 保持
`1.0`，invalid citation `0`。这证明当前已见 candidate 的生成合同更稳定，不证明自由文本
对原始 source 的完整语义蕴含或 unseen 泛化。

Agent task success `0.76 -> 0.84`，provider completeness `0.80 -> 0.88`，
`pass^8 0.76 -> 0.80`；tool selection `0.96`、tool argument `1.0`、state diff `1.0`、
terminal correctness `0.88`、retry idempotency `1.0` 保持。六个 critical workflow 的
pass power 仍为 `1.0`，200/200 trials 完整，重复副作用 `0`，runtime error `0`。

### 3.3 v5 失败分类

只记录安全聚合，不在本文展开 private query、answer、snippet、cases 或 trials：

- Search：6 条 `case-relevance-contract`。
- RAG：1 条 `generation-contract`，case 为 `rag-unseen-097`。该 case 的历史根因仍是
  低于 `0.55` 的 retrieval floor；即使 runner 最终断言名落在 generation contract，也不能
  宣称 retrieval 缺口已修复，更不能通过放宽阈值或修改 gold 消除。
- Agent：4 条 `execution-complete`；其中 3 条同时为 `provider-complete` 与
  `terminal-state`，1 条另有 `tool-selection`。分类可重叠，不能相加成独立分母。

历史 handoff 工具/事件契约差异、Search 六条固定失败、真实 Provider usage/计价缺口仍未由
A2 解决。v5 的改善不能覆盖这些未解决项。

## 4. Evidence custody

- final：
  `/home/song/AI_Shop-unseen-custody-20260829/exploratory-runs/exploratory-external-20260830-v5/`
- runner `SHA256SUMS` SHA-256：
  `c39a74f2b1df1316d3722124dfff9ce383832ffe1c2ab72b62fdc2c9f18a21d8`
- outer `SHA256SUMS` SHA-256：
  `811c2b2cc55a143a661cf31fd08c0c729ac6f2c18ebd6bc63ca335736fee83af`
- 权限：目录 `0700`，文件 `0600`。

runner 原包和 custody 副本均通过 `evaluation.cli verify`；内外 `sha256sum -c`、逐文件
一致性和 runId/split/gate/退出码交叉检查通过。外置包不含 `.env`、`runtime.env`、日志、
PID、锁、build stamp 或 disposable release state。正式五个哨兵运行前后完全一致：

```text
8eed6f9c42df98d721d9092df1687666ce0728bbf351588cecd390dfae43df70  formal release state
d737c11cf8b0d57cce1c28c73479dd7ef3c0d6be1caa24a3ce2d2aef8ba4f0a0  formal consumed-final
94edeb894f3e2d36f597cb9cc9796d6e0e6f6b81da08f030b8723435e441c11a  formal current SHA256SUMS
91c590bc3e8d4f12aa5cf09452b601a7d029158f2dec44c3a1051d5c0468d8d2  central evidence manifest
cefd7d5ded40cf0aa6795ae45e4dba89115a21a8543a4b660105eb85870fef8e  Text2SQL freeze record
```

disposable v5 worktree 在服务停止、runner/custody 双重验证和原子封存后精确移除；v4 worktree
与既有 custody 保持不动。

## 5. 诚实边界与剩余 P2

- v5 不是独立角色生成的 unseen；candidate 已被开发者看见且被 v4/v5 重复使用。
- 评测的结构化 claim/ref/object/value 合同不等于通用自然语言蕴含证明；自由文本语义仍需
  独立 judge 或人工复核。
- verifier 对少数安全 abstention/异值否定会保守拒绝，属于 fail-closed 假阴性 P2。
- verifier 阻断后，部分 WS/run accounting 仍可能记录 `SUCCEEDED`，issue 顺序也可能偏置
  first-cause 指标；v5 Agent 失败分类因此不能包装成正式成功率。
- 本地延迟、短时执行、Provider 完整性与 `pass^8` 都不是生产容量、SLO、CSAT/FCR、
  CTR/CVR/GMV 或绝对安全率。
- Text2SQL、formal unseen、release gate、生产部署、正式 SLO 和多租户路线继续冻结；任何
  重开或晋升必须重新取得用户授权。
