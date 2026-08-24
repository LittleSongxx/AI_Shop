# AI_Shop 文档与证据入口

文档按内容归档，不按日期复制新文件。后续同类变化在同一份稳定文档的“增量记录”中追加；日期只用于说明开发阶段。

| 目录/文件 | 内容 | 证据等级 |
|---|---|---|
| [project/AI_Shop主线与开发记录](project/AI_Shop主线与开发记录.md) | AI 主线、方法判断、关键改造、阶段性取舍和后续路线 | 源码与运行证据汇总 |
| [evaluation/AI质量评测与Badcase](evaluation/AI质量评测与Badcase.md) | Search/RAG/Agent/客服质量、badcase、槽位优化及本地容量边界 | current/archive 与诊断证据包投影 |
| [evaluation/AI质量评测与Badcase.json](evaluation/AI质量评测与Badcase.json) | Search/RAG/Agent 指标、延迟诊断、usage 状态及逐指标 badcase 的机器可读结果 | immutable scorecard 投影 |
| [evaluation/customer-service/客服金标评测](evaluation/customer-service/客服金标评测.md) | 60 条双人盲标+第三人仲裁的 intent、风险、slot、handoff 质量证据 | `HUMAN_VERIFIED` 离线证据，仍不进入 release gate |
| [evaluation/customer-service/客服金标评测.json](evaluation/customer-service/客服金标评测.json) | 客服评测逐 case、canonical slot 诊断和 badcase | 可复核机器证据 |
| [评测输入资产索引](../AI_Shop-backend/AI_Shop-agent/evaluation/README.md) | 可见数据集、私有 holdout、人工标注输入、fixtures、lock 与运行生命周期分类 | 可复现输入入口 |
| [不可变结果资产索引](../AI_Shop-backend/AI_Shop-agent/evaluation-evidence/README.md) | current/archive、人工审查、客服 HTTP、Search/Agent/DB/fault/capacity 结果分类 | `SHA256SUMS` 证据入口 |
| `evaluation-evidence/benchmarks/customer-service/customer-service-answer-review-v2-adjudicated-20260824/` | 历史 v1 60 条 HTTP 最终答案的双盲+第三人仲裁、CI 与 badcase | `HUMAN_REVIEWED_ADJUDICATED`；非 release gate |
| `evaluation-evidence/benchmarks/customer-service/customer-service-http-v13-20260824/` | 修复 sourceRefs 后的 60 条真实 HTTP observation、行为契约与 usage | 原始 report 保持 `PENDING_HUMAN_REVIEW`；执行/契约不等于答案质量 |
| `evaluation-evidence/benchmarks/customer-service/customer-service-http-v13-answer-review-adjudicated-20260824/` | v13 sealed 双评、11 条第三人仲裁、最终指标、CI、badcase 与哈希 | `HUMAN_REVIEWED_ADJUDICATED`；冻结回放证据，非 release gate |
| `evaluation-evidence/benchmarks/customer-service/customer-service-http-v13-answer-review-pending-adjudication-20260824/` | v13 双人 sealed 原件、案件/字段一致性、11 条空白仲裁模板和哈希 | 已完成的只读 parent package，`PENDING_ADJUDICATION`；非 release gate |
| `customer-service-http-v11-targeted-stale-worker-20260824/` + `v12-targeted-after-worker-restart-20260824/` | 独立 MCP 仍加载旧源码的定向复现，以及重启后恢复对照 | 成对、只读 runtime-version 诊断；不进入质量分母 |
| `evaluation/datasets/customer_service/answer-review-v13/` | v13 两份原始空白模板与 manifest（填写版已封存在 evidence package） | 历史导出模板；最终标签只认 adjudicated package |
| [evidence-manifest.json](evidence-manifest.json) | current、archive、visible run、哈希和生命周期机器索引 | 机器校验入口 |

项目根目录的 [Java 后端面试报告](../AI应用开发_Java后端_真实面试题与备考报告_20260824.md) 是独立的求职研究材料，不与项目开发日志重复合并。

## 证据目录边界

| 目录 | 当前内容 | 生命周期 |
|---|---|---|
| `AI_Shop-backend/AI_Shop-agent/evaluation-evidence/current/` | 唯一 current：v9 final（Search 50、RAG 50、Agent 25） | 只读；普通质量主分母 |
| `AI_Shop-backend/AI_Shop-agent/evaluation/.runs/` | v9 development/regression/final 三个可见运行 | 只保留主线运行，旧运行已清理 |
| `evaluation-evidence/archive/` | v2 通过 final、v3-v8 失败 final | 只读；历史审计，不参与 current 分母 |
| `evaluation-evidence/benchmarks/` | 客服 HUMAN_VERIFIED/HTTP、Search paired replay、DB batch/N+1、Agent repeated、fault matrix | 只读辅助证据；不把门禁当质量总分 |
| `AI_Shop-backend/AI_Shop-agent/evaluation/.holdouts/` | final holdout（ignored）及来源指纹 | 不入 Git；只用于一次性 final 追溯 |

只删除已被 v9 主线替代且没有独立审计价值的旧 benchmark、重复 sealed 文件和旧 `.runs`；历史 final、客服 pending/provisional 包保留，
因为它们承担不可变追溯职责。`docs/evidence-manifest.json` 是 current/archive/辅助证据的唯一机器索引，不能通过删除路径来掩盖失败结果。

## 校验与重跑

Python 评测统一使用 Conda `shop` 环境，并在 Agent 目录执行：

```bash
conda activate shop
cd AI_Shop-backend/AI_Shop-agent
python -m evaluation.cli validate
python -m evaluation.cli customer-service-gold --dataset evaluation-evidence/benchmarks/customer-service/customer-service-human-v1-20260823/customer-service-human-v1.jsonl --mode rule
python -m evaluation.cli search-paired-replay --run-id <new-id> --output-dir <new-dir>
python -m evaluation.cli customer-service-http rebuild --source-report <raw-report> --dataset <human-gold> --output-dir <new-dir>
```

当前质量主张的最小复核顺序：先看 `docs/evaluation/AI质量评测与Badcase.md` 的点估计、分母、CI 和 badcase，再看 current 的原始
`cases.jsonl/bad-cases.jsonl/summary.json`；故障矩阵、DB/capacity benchmark、`pass^k` 和本地延迟属于独立诊断，不能替代 Search 或客服质量指标，也不能写成生产 SLO。

客服当前主数据已完成两名标注者盲标和 lead reviewer 冲突仲裁，冻结包位于
`AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/customer-service/customer-service-human-v1-20260823/`；
不得把规则基线、模型自评或 `pass^k` 当成线上成功率；当前 scorecard 同步引用 60 条 HUMAN_VERIFIED 客服 gold，但 `releaseGateEligible=false`，后续标签修订必须生成新版本。

历史 v1 的同一 60 条正式 HTTP Agent 路径证据位于 `customer-service-http-v1-20260823/`；最终答案已由双人盲审和 `8` 条独立第三人仲裁封存为
`HUMAN_REVIEWED_ADJUDICATED`。案件级双人完全一致 `52/60` 是标注可靠性；最终质量为答案正确率 `51/60=85.0%`、引用语义支持
`6/30=20.0%`、转人工适当率 `60/60`、unsafe-answer `0/60`、联合通过 `32/60=53.3%`。该包是固定 HTTP 回放的人工证据，
`releaseGateEligible=false`，不能外推为 CSAT/FCR 或线上客服成功率；引用支持缺口必须在新预注册 holdout 上修复和复验。
修复后的 `customer-service-http-v13-20260824` 已真实执行 `60/60`，并完成动态业务 `sourceRefs` 传播、API/Worker/MCP source fingerprint 预检和免责声明误报评测器修复。其正式包只从原始 Provider observation 做确定性离线重算，`providerCallsReexecuted=false`；源 report 的答案质量字段保持 `PENDING_HUMAN_REVIEW`，但外部双评和 11 条独立第三人仲裁已封存为 `HUMAN_REVIEWED_ADJUDICATED`。最终结果为答案正确 `59/60=98.33%`、可计分引用支持 `20/34=58.82%`、转人工适当 `59/60`、unsafe `0/60`、联合质量 `46/60=76.67%`；14 条 badcase 仍主要是动态订单详情、资格规则、工具能力或后果没有同一行可见证据。不得把 v13 的 `60/60`、行为契约 `10/10`、Intent 指标或本地延迟当作人工答案质量，也不能把与历史 v1 的不同冻结输出包装为严格质量提升；历史 v1 的人工标签不得迁移。
为保留排错证据而不污染主线，v11/v12 的同一 10 条定向对照被单独登记：旧 MCP 运行时包有 `6/10` 行为契约违例，完整重启后恢复包为 `0/10`。它们只证明版本一致性问题曾被复现和消除，不衡量最终答案质量；已被 v13 覆盖的 v5/v10 完整中间运行及其未完成 v3 盲审草稿已删除。
新增 60 条 v2 数据及两份盲标表位于 `evaluation/datasets/customer_service/`，状态为 draft；仲裁前不与 v1 合并。Search 10 条难例回放位于
`evaluation-evidence/benchmarks/search/search-hard-negative-paired-v1-20260823/`，只用于已知难例回归和优化对照，不替代 v9 final。

人工复核工具已内置为 fail-closed 流程：`customer-service-review export` 生成无 expected/预测的双人盲标 sheet，`seal` 生成带哈希的不可变 sheet，`compare` 输出一致性与冲突 badcase，`merge` 只接受两份 sealed sheet，并要求所有冲突有仲裁记录。归档包同时保存 sealed 原件、仲裁文件、合并证据、报告、生命周期和 `SHA256SUMS`。
