A1_STATUS: COMPLETE
EVIDENCE_SCOPE: EXPLORATORY_EXTERNAL_MODEL_ONLY
FORMAL_UNSEEN_STATUS: FROZEN_UNCHANGED
RELEASE_GATE_ELIGIBLE: FALSE
TEXT2SQL_STATUS: FROZEN

# A1 事实 Alias 路由与探索评测

## 实现结论

A1 已在 `db26d436bd381959566ec1d9b2cb9f6727f66d0a` 完成并推送到 `dev`：RAG query
planner 从当前发布版 fact metadata 解析明确引用的长 alias 或完整 fact ID，写入既有
`factHints`；歧义 alias fail-closed，普通未标记提及不扩张。显式术语片段会从后续旧规则
输入中剥离，因此逗号、分号及“然后/之后”等混合意图仍保留第二个业务 hint。语义缓存键
同步纳入 `factHints`，避免复用 A1 前的旧检索结果。

没有新增依赖、事实表、qrel、阈值、知识内容、Text2SQL 代码或正式 unseen 配置。

## 代码验收

- 定向 RAG/metadata/lifecycle 回归：`134 passed`。
- 除两个已知依赖缺失历史 v9 holdout 的 baseline 文件外，Agent 宽回归：
  `1812 passed, 9 skipped`；skip 为真实 MySQL/冻结 Text2SQL 条件。
- Ruff、`git diff --check` 通过；外部 repaired 候选的 29 条可回答 RAG 输入均满足
  `factHints != empty` 且 `factHints ⊆ expected fact IDs`（`29/29`）。
- 两次独立只读审查均确认无 Text2SQL、正式 unseen 或 candidate-ID 硬编码越界。

## v4 探索性外部模型结果

隔离运行：

- release：`exploratory-20260830-mainlines-v4`
- run：`exploratory-external-20260830-v4`
- source：`db26d436bd381959566ec1d9b2cb9f6727f66d0a`
- candidate raw/canonical SHA-256：
  `9769c8e03ffd7a31c48b939c868fda1f1f730f6f47d2f14cbcbfea0623d0168d` /
  `df1e31fde1233b0b5b07d0f45d918572dbdee2812f4ab1f81357ae00e8f94c90`
- runner `SHA256SUMS` SHA-256：
  `ea917960b92f010d184c5f3f9f46928de414527628756d42ab45cec737585180`

结果仍为 `FAILED`，只作开发诊断：

| 域 | v3 | v4 | 结论 |
| --- | --- | --- | --- |
| Search | 44/50 | 44/50 | A1 未改变 Search；六条固定失败不变 |
| RAG | 29/50 | 31/50 | 3 条改善、1 条生成波动回退，净增 2 |
| Agent | 19/25 | 19/25 | 数量不变；terminal `0.84 → 0.88` |

RAG `sourceCoverage/sourcePrecision` 从 `0.586207` 升至 `0.965517`，MRR@10 从
`0.939655` 升至 `0.956897`；但 NDCG@5 从 `0.910388` 降至 `0.858217`，不能写成检索
全面提升。generation/claim/citation 仅从 `0.58` 到 `0.62`，仍低于门槛。唯一 source
coverage 缺口已命中正确 fact 与 Top-1 文档，但 evidence selector 判为 `INSUFFICIENT`；
另一个 v4 回退条目的 hint、Top-1 和引用正确，失败在外部模型输出未命中严格 claim 模式。

Agent 的 handoff 同义词已产生 `HANDOFF` 事件与正确终态，但评测仍期待
`HANDOFF_TO_HUMAN` 工具记录，运行时工具列表为空，属于工具/事件契约差异。六个 critical
action workflow 的 `passPower=1.0`、重复副作用 0；整体 `pass^8=0.76`，不可包装成 Agent
全面通过。

## 候选与证据 custody

repaired-v3 的 `agent-unseen-116` 完整输入已出现在源码回归测试中，生命周期门正确拒绝
认领；没有绕过。外部 `repaired-v4` 仅改写这一条用户措辞，expected/state/safety 不变，
复扫为 0 个暴露 case。该 case 不可用于 v3/v4 同输入比较，RAG 50 条保持不变。

- candidate：`/home/song/AI_Shop-unseen-custody-20260829/exploratory-candidates/repaired-v4/`
- evidence：`/home/song/AI_Shop-unseen-custody-20260829/exploratory-runs/exploratory-external-20260830-v4/`
- runtime quarantine：`/home/song/AI_Shop-exploratory-runtime-quarantine-20260830-v4/`

原 runner 包先通过 CLI verify，复制后再次通过 SHA 与逐文件一致性检查。runtime quarantine
的 510 个文件也已校验，文件/目录权限为 0600/0700。正式 release state、consumed-final、
Agent current、中央 manifest 和原始 candidate 五个哨兵 SHA 在运行前后完全一致。

## 后续路线边界

A1 已完成，不应继续扩大 alias 规则。后续应分别评估：evidence selector 对正确 Top-1 的
`INSUFFICIENT` 判定、严格 claim 生成稳定性、handoff 工具/事件契约，以及 Search 六条
固定失败。任何阈值调整、生成策略改造或评测契约变更都应作为新批次，保留成对证据；
正式 unseen 与 Text2SQL 均保持冻结。
