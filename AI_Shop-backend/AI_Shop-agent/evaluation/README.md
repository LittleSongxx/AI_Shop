# 评测数据集与运行资产

跨 package 的结果、数据集生命周期和提交边界见项目级 [结果与数据集索引](../../../docs/evaluation/结果与数据集索引.md)；当前 v56 人工结果见 [v56 复评记录](../../../docs/evaluation/AI-Shop-v54剩余Badcase修复与v56复评交接-20260827.md)。本文件只说明评测输入和运行目录的职责。

本目录只保存可复现的评测输入、运行生命周期状态和实现代码；不可变结果包统一在相邻的
[`evaluation-evidence/`](../evaluation-evidence/README.md)。已被 source report、`SHA256SUMS` 或
project manifest 绑定的文件不得为了“整理目录”而移动。

| 类别 | 物理位置 | 当前用途与状态 |
|---|---|---|
| 可见 Search/RAG/Agent 输入 | `datasets/development/`、`datasets/regression/` | 当前可复现 development/regression；由 `datasets/locks/*.lock.json` 冻结 |
| Final 输入与消费记录 | `datasets/final-inputs/`、`datasets/locks/consumed-final.json` | final 输入不入 Git；消费过的哈希和生命周期保留，禁止复用；claim 前执行源码暴露 fail-closed 审计 |
| 私有 holdout | `.holdouts/` | v3-v9 的一次性 final 输入，仅本机保留并被 `.gitignore` 排除；v9 已确认可从源码恢复，只能作历史强回归 |
| Final 暴露审计 | `../../../docs/evaluation/final-v9-source-exposure-audit-20260825.json` | 125 条中 120 条暴露，220 个定位/21 个来源文件；覆盖常见文本源码、注释和短精确输入，仅输出安全定位元数据 |
| 人工客服理解金标 | `datasets/customer_service/gold-v1.jsonl`、`datasets/customer_service/adjudicated/gold-v1-human-adjudicated.jsonl` | 60 条意图、风险、槽位、转人工标签；正式副本和 sealed 原件见客服 HUMAN_VERIFIED package |
| 人工客服答案审查 | `datasets/customer_service/adjudicated/answer-review-v2-adjudicated.*` | 本地仅保留运行器读取的 v1 canonical 投影；v1/v13/v20 的 sealed 双评与仲裁、v25 targeted 的 sealed 一致结果和最终标签只保留在对应 immutable evidence package |
| v27 人工答案评测工作区 | `../../../run/review-workspaces/customer-service-http-v27-full-quality-fixes-20260826/` | v27 原始 OPEN 表、人工交回副本和第三人仲裁交接文件；不是 canonical 指标入口，最终结果以 immutable evidence package 为准 |
| v31 HTTP observation 与人工评审工作区 | `../../../run/evaluation-observations/customer-service-http-v31-return-eligibility-20260826.json`、`../../../run/review-workspaces/customer-service-http-v31-return-eligibility-20260826/` | 新真实 Provider 60-case observation；`PENDING_HUMAN_REVIEW`、`selfJudged=false`；两份独立随机 OPEN 表各 60 条，未完成双评/仲裁前不得进入质量分母或替代 v27 canonical 入口 |
| 客服 v2 120 条 canonical | `../evaluation-evidence/benchmarks/customer-service/customer-service-human-v2-provenance-pending-20260826/labels/customer-service-human-v2.jsonl` | SHA `ab5129…079`；hash/label chain 有效，但 reviewer independence 未验证，`releaseGateEligible=false`，只作开发诊断 |
| 客服 v2 标签一致性审计 | `../evaluation-evidence/benchmarks/customer-service/customer-service-human-v2-label-consistency-audit-20260826/` | 5 项发现、25 条受影响、3 项 blocking；taxonomy/slot 指标在 v2.1 独立重仲裁前 fail-closed |
| 客服 v2.1 successor | `datasets/customer_service/adjudicated/customer-service-human-v2.1-human-approved-ai-assisted.jsonl` | 25 条政策复核已完成，20 条 A/B 一致、5 条仲裁；120 条 successor 仍只作开发诊断 |
| 客服 v43 HTTP 与答案证据 | `../evaluation-evidence/benchmarks/customer-service/customer-service-http-v43-human-v2-routing-execution-fix-20260826/`、`../evaluation-evidence/benchmarks/customer-service/customer-service-http-v43-answer-review-human-approved-ai-assisted-20260827/` | 生产执行 120/120、契约 22/22；答案 120 条人工审批、3 条仲裁，联合质量 105/120 |
| v43 badcase 定向修复 | `../evaluation-evidence/benchmarks/customer-service/customer-service-http-v53-badcase-fixes-handoff-evidence-targeted-20260827/` | 15/15 完成生产路径回放，4/4 适用行为契约通过；只是 targeted diagnostic，不改写 v43 人工指标 |
| v54 修复后全量执行与人工结果 | `../evaluation-evidence/benchmarks/customer-service/customer-service-http-v54-full-badcase-fixes-label-evidence-rebuilt-pending-human-review-20260827/`、`../evaluation-evidence/benchmarks/customer-service/customer-service-http-v54-badcase-fixes-answer-review-human-approved-ai-assisted-20260827/` | 保留原始 120 条 HTTP observation，Provider 未重跑；120/120 执行、23/23 契约；A/B 112 条一致、8 条已仲裁，答案 116/120、联合 113/120 |
| v55/v56 剩余 badcase 回归 | `../evaluation-evidence/benchmarks/customer-service/customer-service-http-v55-final-seven-regressions-targeted-pending-human-review-20260827/`、`../evaluation-evidence/benchmarks/customer-service/customer-service-http-v56-full-v3-knowledge-regressions-pending-human-review-20260827/`、`../evaluation-evidence/benchmarks/customer-service/customer-service-http-v56-v3-knowledge-answer-review-human-approved-ai-assisted-20260827/` | v55 目标 7/7；v56 全量 120/120、契约 29/29；A/B 118 条一致、2 条已仲裁，答案/转人工/联合均 120/120，引用 67/67，unsafe 0/120 |
| 外部 unseen final 候选 | 仓库外 `/home/song/AI_Shop-external-unseen-final-20260826/`；登记见 `../../../docs/evaluation/external-unseen-final-registry-20260826.json` | 125 条候选已对开发者可见，永久 `DISQUALIFIED_DEVELOPER_VISIBLE`；只可联调，正式 final 必须由独立保管者生成替代批次 |
| 外部 final 评审协议校验 | `../../../scripts/validate_external_final_review.py` 与 `../../../scripts/test_validate_external_final_review.py` | 专用 `validate/seal/compare`；只检查输入投影、标签结构、哈希和泄漏字段，不读取 `expected` 或计算质量指标 |
| 客服契约与 fixtures | `datasets/customer_service/adjudicated/http-*.json` | HTTP 行为契约、隔离 fixture；仅做受控契约/回归 |
| Search catalog fixture | `fixtures/product-catalog.v1.json`、`fixtures/product-catalog.v2.json` | 离线检索/重放输入，不是生产商品主数据 |
| 历史 RAG 脚本黄金集 | `datasets/legacy/rag_golden.jsonl`、`datasets/legacy/rag_golden.lock.json` | 35 条旧 RAG 诊断输入与原始基线；`SUPERSEDED_LEGACY_DATASET`，不进入 current 指标、发布门禁或求职质量主张 |
| 可见主线运行 | `.runs/development-20260822-ai-quality-v9/`、`.runs/regression-20260822-ai-quality-v9/`、`.runs/final-20260822-ai-quality-v9/` | 三个历史主线 run；每个目录含 `cases.jsonl`、`bad-cases.jsonl`、summary、gates、环境与 `SHA256SUMS`；final 不再称未见 holdout |
| Final 生命周期 | `.state/releases/`、`.state/lifecycle.lock` | v2-v9 的 claim/freeze/消费记录；不是质量结果正文 |

## 人工标注的唯一正式位置

人工提交文件经校验后只有不可变 evidence package 是正式证据；可编辑交接文件统一放在 `../../../run/review-workspaces/`，不是项目指标或生命周期的读取入口：

- 客服理解/路由金标：`../evaluation-evidence/benchmarks/customer-service/customer-service-human-v1-20260823/`。
- 客服 v2 canonical 与来源审计：`../evaluation-evidence/benchmarks/customer-service/customer-service-human-v2-provenance-pending-20260826/`；120 条行标签和合并链可复核，但来源独立性门禁未通过，不是 release/final gold。
- 客服 v2.1 标签政策最终证据：`../evaluation-evidence/benchmarks/customer-service/customer-service-human-v2.1-label-policy-human-approved-ai-assisted-20260827/`；25 条审批和 5 条仲裁已完成，来源独立性仍未通过。
- 历史 HTTP v1 最终答案：`../evaluation-evidence/benchmarks/customer-service/customer-service-answer-review-v2-adjudicated-20260824/`。
- 当前 HTTP v13 最终答案：`../evaluation-evidence/benchmarks/customer-service/customer-service-http-v13-answer-review-adjudicated-20260824/`。
- 历史 HTTP v20 最终答案：`../evaluation-evidence/benchmarks/customer-service/customer-service-http-v20-answer-review-adjudicated-20260825/`；其 pending parent 保留在同名 `-pending-adjudication-20260825/` package。
- 历史 HTTP v27 最终答案：`../evaluation-evidence/benchmarks/customer-service/customer-service-http-v27-full-quality-fixes-answer-review-adjudicated-20260826/`；双评 `58/60`，2 条仲裁，最终答案 `59/60`、引用 `35/36`、联合 `59/60`。
- v31 售后资格修复 observation：`../../../run/evaluation-observations/customer-service-http-v31-return-eligibility-20260826.json`；`cs-gold-v1-001` 保留完整 `WH-1000XM6`，`cs-gold-v1-019/055` 均为 `NO_ELIGIBLE` 且无 `CREATE_SUPPORT_CASE` proposal。v31 的自动执行结果只属于行为/安全诊断，答案人工质量仍为 `PENDING_HUMAN_REVIEW`，不能更新 v27 的 `59/60` 或 `35/36`。
- v31 独立盲审工作区：`../../../run/review-workspaces/customer-service-http-v31-return-eligibility-20260826/`；reviewer-a/b 随机种子不同、各 60 条、标签为空，`containsExpectedOrSelfJudgment=false`，均通过 `review-validate`。
- v25 targeted 最终答案：`../evaluation-evidence/benchmarks/customer-service/customer-service-http-v25-targeted-quality-fixes-answer-review-agreed-20260825/`；两位评审对 10 条完全一致，无分歧、无第三人仲裁，且明确排除普通质量分母。
- v27 最终答案：`../evaluation-evidence/benchmarks/customer-service/customer-service-http-v27-full-quality-fixes-answer-review-adjudicated-20260826/`；双评 58/60 一致，2 条由 `reviewer-c` 仲裁，最终答案正确 59/60、引用支持 35/36、联合质量 59/60。
- v43 历史生产路径与答案：生产报告位于 `customer-service-http-v43-human-v2-routing-execution-fix-20260826/`，最终人工答案包位于 `customer-service-http-v43-answer-review-human-approved-ai-assisted-20260827/`；答案 `107/120`、引用 `66/70`、转人工 `118/120`、unsafe `1/120`、联合 `105/120`。
- v53/v54 修复后证据：v53 的 15 条定向回放只证明已知 badcase 执行和契约回归；v54 完整运行已封存，人工答案证据为 `HUMAN_APPROVED_AI_ASSISTED`，答案 116/120、引用 63/67、转人工 120/120、unsafe 1/120、联合 113/120。交接记录见 `../../../docs/evaluation/AI-Shop-v43-Badcase修复与v54复评交接-20260827.md`。
- v56 最终答案：`../evaluation-evidence/benchmarks/customer-service/customer-service-http-v56-v3-knowledge-answer-review-human-approved-ai-assisted-20260827/`；A/B 各 120 条、118 条一致，`cs-gold-v1-026`、`cs-candidate-v2-096` 已人工仲裁；答案/转人工/联合 120/120、引用 67/67、unsafe 0/120、badcase 0，仍只作已见开发集诊断。
- 各 answer-review package 的 pending 目录是 sealed 双评和空白仲裁模板的不可变 parent，不是待继续编辑的工作区。

`customer_service/adjudicated/` 仅保留运行器读取所需的 canonical 复用投影；它不能替代对应 evidence package 的
sealed review、adjudication、最终报告和 `SHA256SUMS`。

## 2026-08-25 定向修复证据

- Search v4：`../../../run/evaluation-observations/search-hard-negative-paired-v4-20260825/`，10 条已知 v9 难例的 paired replay；不进入普通质量分母，不能称新 final。
- Android 约束 v24：`../evaluation-evidence/benchmarks/customer-service/customer-service-http-v24-cs043-context-bound-probe-20260825/`，单 case 请求绑定约束探针；答案质量待人工评审。
- 客服 v25：`../evaluation-evidence/benchmarks/customer-service/customer-service-http-v25-targeted-quality-fixes-20260825/` 是 10 条 targeted execution；只有 `019/055` 两条行为契约实际执行。源执行报告保留生成时的 `PENDING_HUMAN_REVIEW`，后续双评已在独立 agreed package 完成：10/10 完全一致、0 分歧、0 仲裁，四项正向质量 10/10，unsafe 0/10；Wilson 区间和逐 case 绑定以最终包为准。
- open-arrival 容量 v1/v2：`../evaluation-evidence/benchmarks/capacity/capacity-open-arrival-readonly-v{1,2}-20260825/`；v1 保留错误 Provider 契约事实，v2 是修正后的本地容量观察，二者都不是生产 SLO。

旧 NDCG 和容量 evidence 保持 immutable，没有用新评测器覆盖或静默重算。新 NDCG 使用同一候选 graded gains 的理想降序；新延迟报告显式记录 evaluator polling/settle 开销。完整方法、联网依据和剩余缺口见
[`质量缺口审计与优化-20260825.md`](../../../docs/evaluation/质量缺口审计与优化-20260825.md)。

## 2026-08-27 v43 badcase 修复与 v54 复评

- v43 的 15 条人工 badcase 已逐条建立根因、修复和 v54 观测映射，见 [`AI-Shop-v43-Badcase修复与v54复评交接-20260827.md`](../../../docs/evaluation/AI-Shop-v43-Badcase修复与v54复评交接-20260827.md)。
- `shouldHandoff` 仅评初始 API intent 路由；解析订单/地址后才触发的转接单独记为最终支持转接，并在答案双盲评审中判断是否适当。
- v54 人工结果已由独立包承载；仍不得以自动 verifier、23/23 契约或 120/120 执行代替该人工指标，也不得将已见集结果表述为 unseen 泛化。

## 使用边界

Text2SQL V0 使用独立模块 [`text2sql/README.md`](text2sql/README.md)，不加入既有
Search/RAG/Agent `Domain` 或 lock。80 条 gold、修复前后各 80×3 基线以及 canonical 输出
A/B/C 人工流程均已封存；最终入口是
`../evaluation-evidence/benchmarks/text2sql/final-v0-20260828-run-002/`。修复后仍有
51/80 个 canonical 输出被真人拒绝，固定为 `DEVELOPMENT / PROVISIONAL`，不得解释为发布就绪。

- 新开发/回归 case 只新增到可见 split，并更新 lock；新 final 只能通过 holdout lifecycle 生成。
- 新人工标签必须经历 `OPEN -> SEALED -> HUMAN_VERIFIED` 或 `HUMAN_REVIEWED_ADJUDICATED`，不能覆盖已有 JSONL。
- 自动 verifier、引用结构校验和行为契约不能替代双盲人工答案正确性、引用语义支持与 unsafe answer 评审。
- paired/targeted replay 只能报告已知集合回归，不能表述为 holdout、泛化或线上质量提升。
- 规则预路由 Intent/Slot、行为契约全通过率、`pass^k` 和案件级 reviewer agreement 只能作诊断或证据流程可靠性，不得写成真实最终答案质量。
- `datasets/legacy/` 只为完整保留历史脚本输入；不得以其中旧基线复述 current RAG 质量，也不得扩充或重新消费为 final。
- 结果、badcase、CI 与人工标签从 evidence package 读取；目录分类与完整文件清单见
  [`evaluation-evidence/README.md`](../evaluation-evidence/README.md) 和
  [`docs/evidence-manifest.json`](../../../docs/evidence-manifest.json)。
