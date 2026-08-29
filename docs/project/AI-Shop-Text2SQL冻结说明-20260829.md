# Text2SQL 战略冻结说明

> 生效日期：2026-08-29（Asia/Shanghai）
> 冻结基线：`567442b`
> 维护者：仅限用户明确重新授权

```text
TEXT2SQL_STATUS: FROZEN
TEXT2SQL_SCOPE: NARRATIVE_AND_ROADMAP_ONLY
TEXT2SQL_CODE_CHANGES: PROHIBITED
TEXT2SQL_UNSEEN: PAUSED
TEXT2SQL_RELEASE_GATE: FALSE
```

## 冻结边界

Text2SQL 从产品主线和默认求职演示中移除，降为内部治理实验与兼容能力。冻结期间：

- 不新增 compiler、语义视图、DDL、reader、权限范围或生产部署。
- 不运行 unseen、在线实验或新的 release-gate 评测。
- 不修改、重算、合并或删除已经封存的 Text2SQL 证据包。
- 只允许阻止现有功能回归的兼容性维护；任何功能扩展都视为重新开线。
- 保留 DataAnalyst、InventoryOps 及共享 analytics 代码，避免破坏既有依赖。

## 证据 custody

当前新一批证据位于默认输出根目录：

`AI_Shop-backend/evaluation-evidence/benchmarks/text2sql/`

该目录目前为本机未跟踪材料：17 个评测包，另有 1 个仅含 `verification.json`/`SHA256SUMS` 的验证记录目录（目录合计 18 个），共 493 个文件、48,561,657 bytes；包内均标记为 development/provisional，`unseen=false`、`releaseGateEligible=false`。最新阶段为 `post-quality-compiler-v0-20260829-run-002`；最终验证仍为 `EXTERNAL_PRIVATE_HOLDOUT_MISSING`。

原始包不进入 Git，不去重、不改路径。需要交接时，只提交本文件或单独的路径/SHA 指针，不提交 SQL、结果行、review 原件或响应原文。

## F0 只读基线红项

接手盘点基线为 `dev` 分支、`567442b`；当时相对 `origin/dev` 有 9 个待推送提交。执行
`python scripts/check_evidence_manifest.py --require-current` 的结果保持非零，已知红项仅作为
历史基线记录：不可变证据包仍有可写文件、`visibleRuns.development/regression` 目录缺失、评测
资产目录/根清理状态不一致、旧 `benchmarks`/`app/evaluation` 路径残留，以及被用户决定删除的
`AI应用开发_Java后端_真实面试题与备考报告_20260824.md` 链接和 claim 缺失。未通过 chmod、补空
目录、改历史包或重写 manifest 来消除这些红项；本次后续提交没有新增 Text2SQL 代码或证据错误。

本次清理盘点发现 22 个过期的本地 privacy export（约 37 KB）以及若干 `run/` 运行日志；盘点时本机仍有 AI_Shop 容器运行，因此这些文件暂不移动或删除。privacy export 路径已加入 `.gitignore`，待服务停机后再按可逆 quarantine 流程处理。`run/secrets/`、`run/data`、`run/backups` 和 `run/recovery` 不属于可直接清理范围。

## 后续排期

求职建设只围绕两条产品闭环：

1. AI 购物导购：文本/视觉检索 → Java 商品、库存、报价事实 → 约束解释 → 点击/加购归因。
2. AI 客服 Agent：意图 → 发布版 RAG 与 Java 订单事实 → 用户确认 → 幂等执行 / `INCONCLUSIVE` / `MANUAL_REVIEW`。

后续工程优先级为：真实流式与重连、取消/终态/幂等、Action 快照与对账、检查点/记忆、模型与工具治理、身份与安全、RAG 生命周期、可观测与背压、前端真实浏览器/a11y/perf。Text2SQL 不占用这些批次的排期。

## 重新开线条件

必须同时满足：用户明确授权；独立 holdout 与源码暴露/重叠审计；语义双盲与仲裁；证据存储和隐私策略重新确认；并单独建立新的 handoff 与 release 边界。仅有现有 development/provisional 指标不得自动恢复。
