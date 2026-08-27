# 不可变评测结果与人工证据

本目录按结果生命周期保存。每个 package 的 `SHA256SUMS` 是完整文件清单和完整性锚点；不要修改、合并或移动包内文件。
`docs/evidence-manifest.json` 是跨 package 的机器索引，`evaluation/README.md` 是可复现输入索引。

| 分类 | 位置 | 保留含义 |
|---|---|---|
| 唯一 current final | `current/` | `final-20260822-ai-quality-v9`，Search 50 / RAG 50 / Agent 25；唯一 current，不包含客服人工答案质量分母 |
| 历史 final 审计 | `archive/final-20260820-ai-quality-v2/` 至 `archive/final-20260822-ai-quality-v8/` | 已消费的历史 final，包含通过和失败记录；只读，不重算、不覆盖 |
| 可见运行原件 | `../evaluation/.runs/` | v9 development、regression、final 的完整 cases、badcases、gates、trials 与环境；与 current/发布包共同保留 |
| 客服人工理解金标 | `benchmarks/customer-service/customer-service-human-v1-20260823/` | 60 条双盲+仲裁的 intent/risk/slot/handoff gold，`HUMAN_VERIFIED` |
| 客服 v2 来源链 | `benchmarks/customer-service/customer-service-human-v2-provenance-pending-20260826/` | 120 条 canonical、26 条 additions 仲裁和完整 hash chain；独立性/OPEN 来源控制未通过，`releaseGateEligible=false` |
| 客服 v2 历史回传恢复归档 | `intake-archive/customer-service-v2-additions-original-submissions-recovered-20260826/` | 根目录发现的 reviewer-a/b 各 60 条已填写回传件、原 manifest、sealed binding 与补充审计；bytes 已恢复，export-hash 语义及独立性仍阻断 |
| 客服 v2 标签一致性 | `benchmarks/customer-service/customer-service-human-v2-label-consistency-audit-20260826/` | 5 项跨 case taxonomy/slot 政策发现、25 条重仲裁模板；阻断 strict 指标与发布门禁 |
| 当前客服 v2.1 人工审批标签 | `benchmarks/customer-service/customer-service-human-v2.1-label-policy-human-approved-ai-assisted-20260827/` | 25 条政策复核中 20 条 A/B 一致、5 条仲裁、19 条相对 v2 变化；发布 120 条 successor；槽位 F1 `0.482551`、EM `0.426471`，非 release gate |
| 客服 v2 修复 paired | `benchmarks/customer-service/customer-service-v2-routing-paired-fix-validity-gated-20260826/` | 同一暴露 120 条的前后配对统计；证明 development fix，不证明 unseen 泛化 |
| 客服 HTTP 答案人工审查 | `benchmarks/customer-service/customer-service-answer-review-v2-adjudicated-20260824/`、`customer-service-http-v13-answer-review-adjudicated-20260824/` | 分别绑定 v1 与 v13 的最终答案、sealed 双评、第三人仲裁、CI 和逐 case badcase，均 `HUMAN_REVIEWED_ADJUDICATED` |
| 客服审查 parent | `customer-service-answer-review-v2-pending-adjudication-20260824/`、`customer-service-http-v13-answer-review-pending-adjudication-20260824/`、`customer-service-review-v1-pending-adjudication/` | 不可变双评/模板 parent，保留以追溯最终仲裁来源；不能重新填写 |
| 客服 HTTP 与排错证据 | `customer-service-http-v1-20260823/`、`customer-service-http-v13-20260824/`、`customer-service-http-v13-pre-evaluator-fix-20260824/`、`customer-service-http-v14-20260825/`、`customer-service-http-v20-20260825/`、`customer-service-http-v24-cs043-context-bound-probe-20260825/`、`customer-service-http-v25-targeted-quality-fixes-20260825/`、`customer-service-http-v11-targeted-stale-worker-20260824/`、`customer-service-http-v12-targeted-after-worker-restart-20260824/`、`customer-service-slot-replay-v1-20260823/` | v14 是真实 live observation；v20 是历史保留 observation 的离线重算；v24/v25 是定向探针/回归；v27 人工质量另有 immutable package；均为诊断/追溯，不自动成为 release gate |
| 历史 v20 答案人工证据 | `customer-service-http-v20-answer-review-pending-adjudication-20260825/`、`customer-service-http-v20-answer-review-adjudicated-20260825/` | 双人 sealed 原件、56/60 一致、4 条第三人仲裁、最终 57/60 答案正确和 11 条 badcase；保留作历史回归，仍 `releaseGateEligible=false` |
| 历史 v27 答案人工证据 | `customer-service-http-v27-full-quality-fixes-answer-review-pending-adjudication-20260826/`、`customer-service-http-v27-full-quality-fixes-answer-review-adjudicated-20260826/` | 60 条冻结 HTTP 输出；双评 58/60 一致、2 条 `reviewer-c` 仲裁；答案 `59/60`、引用 `35/36`、联合 `59/60`；`HUMAN_REVIEWED_ADJUDICATED`、非 release gate；源 report 在 ignored `run/` |
| 历史 v43 生产路径证据 | `customer-service-http-v43-human-v2-routing-execution-fix-20260826/` | 120/120 生产 episode、22/22 行为契约；生成时答案语义字段保持 `null`，最终人工结果由独立包承载 |
| 历史 v43 人工答案证据 | `customer-service-http-v43-answer-review-pending-adjudication-20260827/`、`customer-service-http-v43-answer-review-human-approved-ai-assisted-20260827/` | A/B 117/120 一致、3 条仲裁；答案 `107/120`、引用 `66/70`、转人工 `118/120`、unsafe `1/120`、联合 `105/120`；人工最终决策、AI 辅助整理，非 release gate |
| v43 badcase 定向修复证据 | `customer-service-http-v53-badcase-fixes-handoff-evidence-targeted-20260827/` | 15/15 生产路径执行、4/4 适用行为契约通过；`normalQualityDenominatorExcluded=true`，不改写 v43 人工指标 |
| v54 修复后完整执行 | `customer-service-http-v54-full-badcase-fixes-pending-human-review-20260827/`、`customer-service-http-v54-full-badcase-fixes-label-evidence-rebuilt-pending-human-review-20260827/` | 原始 observation 执行 120/120、行为契约 23/23；后者未重跑 Provider，仅离线重建并绑定 v2.1 人工标签证据。源包保留生成时 pending 状态，最终人工指标在独立包 |
| v54 答案人工最终证据 | `customer-service-http-v54-badcase-fixes-answer-review-human-approved-ai-assisted-20260827/` | A/B `112/120` 一致、8 条仲裁；答案 `116/120`、引用 `63/67`、转人工 `120/120`、unsafe `1/120`、联合 `113/120`；7 条 badcase，非 release/unseen |
| v54 答案回传与 pending parent | `../evaluation-evidence/intake-archive/customer-service-v54-answer-review-round1-returns-human-approved-ai-assisted-20260827/`、`../evaluation-evidence/intake-archive/customer-service-v54-answer-review-adjudication-return-human-approved-ai-assisted-20260827/`、`customer-service-http-v54-badcase-fixes-answer-review-pending-adjudication-20260827/` | A/B 与 C 原始 ZIP/回传已保存；人工决策、AI 辅助编辑；A/B 规范化仅恢复 4 条 JSON 数值表示，C 冻结字段改动为 0 |
| v55 剩余 7 条定向复评 | `customer-service-http-v55-final-seven-regressions-targeted-pending-human-review-20260827/` | 7/7 生产路径执行、7/7 适用 v2.3 契约通过；只作 targeted regression，答案语义仍待人审 |
| v56 v3 知识全量复评 | `customer-service-http-v56-full-v3-knowledge-regressions-pending-human-review-20260827/`、`customer-service-http-v56-v3-knowledge-answer-review-pending-adjudication-20260827/`、`customer-service-http-v56-v3-knowledge-answer-review-human-approved-ai-assisted-20260827/` | 120/120 执行、29/29 契约；A/B 118/120 一致、2 条人工仲裁；答案 120/120、引用 67/67、转人工 120/120、unsafe 0/120、联合 120/120、badcase 0；非 release/unseen |
| v56 人工回传归档 | `../evaluation-evidence/intake-archive/customer-service-v56-answer-review-round1-returns-human-approved-ai-assisted-20260827/`、`../evaluation-evidence/intake-archive/customer-service-v56-answer-review-adjudication-return-human-approved-ai-assisted-20260827/` | A/B 与 C 原始 ZIP/回传均保存；人工决策、AI 辅助编辑；冻结字段改动为 0 |
| 当前人工回传与审批来源 | `intake-archive/human-review-adjudication-returns-20260827/`、`intake-archive/human-review-human-approval-ai-assistance-provenance-20260827/` | 原始仲裁 bytes、审批澄清、身份错位审计和只改身份的派生规范化绑定；证据等级 `HUMAN_APPROVED_AI_ASSISTED` |
| v31 售后资格修复 observation | `../run/evaluation-observations/customer-service-http-v31-return-eligibility-20260826.json` | 真实 Provider 60-case 新 observation；HTTP `60/60`、10/10 behavior contract、fixture cleanup `0` 仅属执行/安全诊断；答案质量 `PENDING_HUMAN_REVIEW`，不登记为 immutable canonical quality package |
| v31 独立盲审工作区 | `../run/review-workspaces/customer-service-http-v31-return-eligibility-20260826/` | reviewer-a/b 各 60 条、独立随机顺序、空白标签；双评和仲裁完成前不是 evidence package，不得修改 v27 包 |
| v25 定向答案人工证据 | `customer-service-http-v25-targeted-quality-fixes-answer-review-agreed-20260825/` | 绑定 v25 原始 10 条 observation；双人 sealed 原件、10/10 完全一致、0 分歧，因此没有第三人仲裁；四项正向质量 10/10、unsafe 0/10，`normalQualityDenominatorExcluded=true` |
| Search 诊断 | `benchmarks/search/search-hard-negative-paired-v1-20260823/` | 10 条已知难例的成对无回归重放，不替代 final |
| Agent 可靠性 | `benchmarks/repeated-agent/agent-pass5-development-v9-20260822/`、`agent-pass5-regression-v9-20260822/` | 重复试验、终态、state diff 与幂等证据；可靠性门禁，不是质量展示分数 |
| 故障恢复 | `benchmarks/resilience/fault-v9-20260822/` | 12 个故障注入 recovery contract，独立于正常质量分母 |
| 数据库性能 | `benchmarks/db/db-benchmark-v9-20260822/` | 隔离 MySQL batch/N+1、round trip、rollback benchmark；不是生产 SLO |
| 容量诊断 | `benchmarks/capacity/capacity-readonly-v1-20260823/` 至 `capacity-readonly-v5-20260824/`、`capacity-social-shortcut-v1-20260823/` 至 `capacity-social-shortcut-v4-20260823/`，以及 `capacity-open-arrival-readonly-v1-20260825/`、`capacity-open-arrival-readonly-v2-20260825/` | v5 是 closed-model 并发阶梯；open-arrival v2 是当前固定到达率观察，v1 保留错误契约事实；均不作为生产容量结论 |

## 结果文件约定

- 常规 run package：`cases.jsonl`、`bad-cases.jsonl`、`summary.json`、`gates.json`、环境/源码指纹、`report.md`、`SHA256SUMS`；final/repeat 会额外包含 `trials.jsonl` 和生命周期记录。
- 人工审查 package：sealed reviewer 原件、agreement、`final-report.json`/`badcases.jsonl`、evidence manifest 与 `SHA256SUMS`；只有存在分歧时才应包含独立 adjudication。
- benchmark package：原始 observations/cases、机器报告、可读 report、evidence manifest 与 `SHA256SUMS`。

验证前先运行项目总清单校验，再验证某个人工答案 package：

```bash
cd /home/song/code/Java/AI_Shop
conda run -n shop python scripts/check_evidence_manifest.py

cd AI_Shop-backend/AI_Shop-agent
conda run -n shop python -m evaluation.cli customer-service-http review-verify \
  --evidence-dir evaluation-evidence/benchmarks/customer-service/customer-service-http-v43-answer-review-human-approved-ai-assisted-20260827
```

这些本地运行、容量和 usage 数据都是可复核诊断，不得改写为线上 SLO、持续容量、CTR/CVR/GMV、CSAT、FCR 或零成本结论。
