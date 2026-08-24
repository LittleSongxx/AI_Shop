# 评测数据集与运行资产

本目录只保存可复现的评测输入、运行生命周期状态和实现代码；不可变结果包统一在相邻的
[`evaluation-evidence/`](../evaluation-evidence/README.md)。已被 source report、`SHA256SUMS` 或
project manifest 绑定的文件不得为了“整理目录”而移动。

| 类别 | 物理位置 | 当前用途与状态 |
|---|---|---|
| 可见 Search/RAG/Agent 输入 | `datasets/development/`、`datasets/regression/` | 当前可复现 development/regression；由 `datasets/locks/*.lock.json` 冻结 |
| Final 输入与消费记录 | `datasets/final-inputs/`、`datasets/locks/consumed-final.json` | final 输入不入 Git；消费过的哈希和生命周期保留，禁止复用 |
| 私有 holdout | `.holdouts/` | v3-v9 的一次性 final 输入，仅本机保留并被 `.gitignore` 排除；哈希见 lifecycle/manifest |
| 人工客服理解金标 | `datasets/customer_service/gold-v1.jsonl`、`datasets/customer_service/adjudicated/gold-v1-human-adjudicated.jsonl` | 60 条意图、风险、槽位、转人工标签；正式副本和 sealed 原件见客服 HUMAN_VERIFIED package |
| 人工客服答案审查 | `datasets/customer_service/adjudicated/answer-review-v2-adjudicated.*`、`datasets/customer_service/answer-review-v2/`、`datasets/customer_service/answer-review-v13/` | v1/v13 最终答案标签严格绑定各自 HTTP report 与 answer SHA-256；目录内 open sheet 只是历史导出模板，不是最终标签 |
| 客服候选扩展集 | `datasets/customer_service/candidate-v2-additions.*`、`annotation-v2/` | 60 条 draft 与空白双盲模板；`DRAFT_NEEDS_DUAL_HUMAN_REVIEW`，不得进入任何当前质量分母 |
| 客服契约与 fixtures | `datasets/customer_service/adjudicated/http-*.json` | HTTP 行为契约、隔离 fixture；仅做受控契约/回归 |
| Search catalog fixture | `fixtures/product-catalog.v1.json`、`fixtures/product-catalog.v2.json` | 离线检索/重放输入，不是生产商品主数据 |
| 历史 RAG 脚本黄金集 | `datasets/legacy/rag_golden.jsonl`、`datasets/legacy/rag_golden.lock.json` | 35 条旧 RAG 诊断输入与原始基线；`SUPERSEDED_LEGACY_DATASET`，不进入 current 指标、发布门禁或求职质量主张 |
| 可见主线运行 | `.runs/development-20260822-ai-quality-v9/`、`.runs/regression-20260822-ai-quality-v9/`、`.runs/final-20260822-ai-quality-v9/` | 三个当前可见 run；每个目录含 `cases.jsonl`、`bad-cases.jsonl`、summary、gates、环境与 `SHA256SUMS` |
| Final 生命周期 | `.state/releases/`、`.state/lifecycle.lock` | v2-v9 的 claim/freeze/消费记录；不是质量结果正文 |

## 人工标注的唯一正式位置

人工提交文件在校验后只保留在不可变 evidence package 中，避免根目录、临时路径和正式证据并存多个可编辑副本：

- 客服理解/路由金标：`../evaluation-evidence/benchmarks/customer-service/customer-service-human-v1-20260823/`。
- 历史 HTTP v1 最终答案：`../evaluation-evidence/benchmarks/customer-service/customer-service-answer-review-v2-adjudicated-20260824/`。
- 当前 HTTP v13 最终答案：`../evaluation-evidence/benchmarks/customer-service/customer-service-http-v13-answer-review-adjudicated-20260824/`。
- 两个 answer-review package 的 pending 目录是 sealed 双评和空白仲裁模板的不可变 parent，不是待继续编辑的工作区。

`customer_service/adjudicated/` 仅保留运行器读取所需的 canonical 复用投影；它不能替代对应 evidence package 的
sealed review、adjudication、最终报告和 `SHA256SUMS`。

## 使用边界

- 新开发/回归 case 只新增到可见 split，并更新 lock；新 final 只能通过 holdout lifecycle 生成。
- 新人工标签必须经历 `OPEN -> SEALED -> HUMAN_VERIFIED` 或 `HUMAN_REVIEWED_ADJUDICATED`，不能覆盖已有 JSONL。
- `datasets/legacy/` 只为完整保留历史脚本输入；不得以其中旧基线复述 current RAG 质量，也不得扩充或重新消费为 final。
- 结果、badcase、CI 与人工标签从 evidence package 读取；目录分类与完整文件清单见
  [`evaluation-evidence/README.md`](../evaluation-evidence/README.md) 和
  [`docs/evidence-manifest.json`](../../../docs/evidence-manifest.json)。
