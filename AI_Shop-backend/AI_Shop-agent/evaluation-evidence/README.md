# 不可变评测结果与人工证据

本目录按结果生命周期保存。每个 package 的 `SHA256SUMS` 是完整文件清单和完整性锚点；不要修改、合并或移动包内文件。
`docs/evidence-manifest.json` 是跨 package 的机器索引，`evaluation/README.md` 是可复现输入索引。

| 分类 | 位置 | 保留含义 |
|---|---|---|
| 唯一 current final | `current/` | `final-20260822-ai-quality-v9`，Search 50 / RAG 50 / Agent 25；唯一 current，不包含客服人工答案质量分母 |
| 历史 final 审计 | `archive/final-20260820-ai-quality-v2/` 至 `archive/final-20260822-ai-quality-v8/` | 已消费的历史 final，包含通过和失败记录；只读，不重算、不覆盖 |
| 可见运行原件 | `../evaluation/.runs/` | v9 development、regression、final 的完整 cases、badcases、gates、trials 与环境；与 current/发布包共同保留 |
| 客服人工理解金标 | `benchmarks/customer-service/customer-service-human-v1-20260823/` | 60 条双盲+仲裁的 intent/risk/slot/handoff gold，`HUMAN_VERIFIED` |
| 客服 HTTP 答案人工审查 | `benchmarks/customer-service/customer-service-answer-review-v2-adjudicated-20260824/`、`customer-service-http-v13-answer-review-adjudicated-20260824/` | 分别绑定 v1 与 v13 的最终答案、sealed 双评、第三人仲裁、CI 和逐 case badcase，均 `HUMAN_REVIEWED_ADJUDICATED` |
| 客服审查 parent | `customer-service-answer-review-v2-pending-adjudication-20260824/`、`customer-service-http-v13-answer-review-pending-adjudication-20260824/`、`customer-service-review-v1-pending-adjudication/` | 不可变双评/模板 parent，保留以追溯最终仲裁来源；不能重新填写 |
| 客服 HTTP 与排错证据 | `customer-service-http-v1-20260823/`、`customer-service-http-v13-20260824/`、`customer-service-http-v13-pre-evaluator-fix-20260824/`、`customer-service-http-v14-20260825/`、`customer-service-http-v20-20260825/`、`customer-service-http-v11-targeted-stale-worker-20260824/`、`customer-service-http-v12-targeted-after-worker-restart-20260824/`、`customer-service-slot-replay-v1-20260823/` | v14 是真实 live observation；v20 是当前保留 observation 的离线重算；另含评测器修复前快照、运行时版本对照和 slot paired replay；均为诊断/追溯，不自动成为 release gate |
| 当前 v20 答案人工证据 | `customer-service-http-v20-answer-review-pending-adjudication-20260825/`、`customer-service-http-v20-answer-review-adjudicated-20260825/` | 双人 sealed 原件、56/60 一致、4 条第三人仲裁、最终 57/60 答案正确和 11 条 badcase；最终生命周期 `HUMAN_REVIEWED_ADJUDICATED`，仍 `releaseGateEligible=false` |
| Search 诊断 | `benchmarks/search/search-hard-negative-paired-v1-20260823/` | 10 条已知难例的成对无回归重放，不替代 final |
| Agent 可靠性 | `benchmarks/repeated-agent/agent-pass5-development-v9-20260822/`、`agent-pass5-regression-v9-20260822/` | 重复试验、终态、state diff 与幂等证据；可靠性门禁，不是质量展示分数 |
| 故障恢复 | `benchmarks/resilience/fault-v9-20260822/` | 12 个故障注入 recovery contract，独立于正常质量分母 |
| 数据库性能 | `benchmarks/db/db-benchmark-v9-20260822/` | 隔离 MySQL batch/N+1、round trip、rollback benchmark；不是生产 SLO |
| 容量诊断 | `benchmarks/capacity/capacity-readonly-v1-20260823/` 至 `capacity-readonly-v5-20260824/`，以及 `capacity-social-shortcut-v1-20260823/` 至 `capacity-social-shortcut-v4-20260823/` | 当前只读容量口径为 v5；较早版本和 social probe 是排错历史，不作为生产容量结论 |

## 结果文件约定

- 常规 run package：`cases.jsonl`、`bad-cases.jsonl`、`summary.json`、`gates.json`、环境/源码指纹、`report.md`、`SHA256SUMS`；final/repeat 会额外包含 `trials.jsonl` 和生命周期记录。
- 人工审查 package：sealed reviewer 原件、adjudication、agreement、`final-report.json`/`badcases.jsonl`、evidence manifest 与 `SHA256SUMS`。
- benchmark package：原始 observations/cases、机器报告、可读 report、evidence manifest 与 `SHA256SUMS`。

验证前先运行项目总清单校验，再验证某个人工答案 package：

```bash
cd /home/song/code/Java/AI_Shop
conda run -n shop python scripts/check_evidence_manifest.py

cd AI_Shop-backend/AI_Shop-agent
conda run -n shop python -m evaluation.cli customer-service-http review-verify \
  --evidence-dir evaluation-evidence/benchmarks/customer-service/customer-service-http-v13-answer-review-adjudicated-20260824
```

这些本地运行、容量和 usage 数据都是可复核诊断，不得改写为线上 SLO、持续容量、CTR/CVR/GMV、CSAT、FCR 或零成本结论。
